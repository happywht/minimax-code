"""Process crash regression: a committed request must never replay its write."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CHILD = r'''
import asyncio
import io
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from minimax_code import agent, app
from minimax_code.config import Config
from minimax_code.ipc import builtins
from minimax_code.ipc.handlers_runs import register_run_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO


class Context:
    def __init__(self, server):
        self.server = server
        self.reply = AsyncMock()
        self.reply_error = AsyncMock()
        self.emit = AsyncMock()


class Core:
    def __init__(self, **kwargs):
        pass

    def cancel(self):
        pass

    async def run(self, **kwargs):
        await self.on_tool_call({"id": "write", "name": "write_file", "args": {"path": "effect.txt"}})
        with Path("effect.txt").open("a", encoding="utf-8") as stream:
            stream.write("written once\n")
            stream.flush()
            os.fsync(stream.fileno())
        os._exit(23)  # no finalizer, shutdown hook or receipt can run


async def main():
    db = await app.ensure_db()  # real startup recovery, migration and storage
    agent.AgentCore = Core
    app.init_runtime = AsyncMock()
    app.get_subagent_llm = lambda: SimpleNamespace(default_model="test", mock=True)
    builtins._build_system_prompt_extra = AsyncMock(return_value=None)
    app.ensure_codebase_indexer = lambda *args: None
    app.get_codebase_indexer = lambda: None
    app.ensure_hook_manager = lambda: None
    server = IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO())
    register_run_handlers(server)
    params = {"session_id": "crash-session", "content": "write once", "idempotency_key": "stable-key"}
    if sys.argv[1] == "crash":
        await SessionsDAO(db).create(id=params["session_id"], workspace_path=str(Path.cwd()))
        await builtins.handle_agent_send_message(params, Context(server))
        raise AssertionError("Expected forced process exit during execution")
    ctx = Context(server)
    await server._handlers["run.start"](params, ctx)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.call_args.args[0]
    dao = AgentRunsDAO(db)
    snapshot = await dao.snapshot(receipt["run"]["id"])
    legacy = Context(server)
    await builtins.handle_agent_send_message(params, legacy)
    print(json.dumps({"admission": receipt, "snapshot": snapshot,
                      "legacy_error": legacy.reply_error.call_args.args,
                      "count": len(await dao.list_runs()),
                      "result": await dao.result_payload(receipt["run"]["id"])}), flush=True)
    await db.close()


try:
    asyncio.run(main())
except BaseException:
    import traceback
    traceback.print_exc()
    sys.stderr.flush()
    os._exit(1)  # fixture failures must not strand an open SQLite worker thread
'''


def test_restart_recovers_request_without_replaying_uncertain_write(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               MINIMAX_CODE_DATA_DIR=str(tmp_path / "data"), MINIMAX_CODE_WORKSPACE=str(tmp_path),
               MINIMAX_CODE_FORCE_MOCK="1")
    first = subprocess.run([sys.executable, "-c", CHILD, "crash"], cwd=tmp_path,
                           env=env, capture_output=True, text=True, timeout=30)
    assert first.returncode == 23, first.stderr
    second = subprocess.run([sys.executable, "-c", CHILD, "recover"], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    result = json.loads(second.stdout.strip().splitlines()[-1])
    assert result["admission"]["created"] is False
    assert result["admission"]["run"]["status"] == "interrupted"
    assert result["count"] == 1 and result["result"] is None
    assert result["legacy_error"][2]["status"] == "interrupted"
    steps = result["snapshot"]["steps"]
    assert any(s["tool_call_id"] == "write" and s["status"] == "failed" for s in steps)
    assert any((s.get("payload") or {}).get("requires_review") for s in steps)
    assert (tmp_path / "effect.txt").read_text(encoding="utf-8") == "written once\n"
