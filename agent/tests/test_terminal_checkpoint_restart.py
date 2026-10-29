"""Durable terminal output survives an unclean agent process exit."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

CHILD = r'''
import asyncio, json, os, sys
from pathlib import Path
from minimax_code import app
from minimax_code.ipc import handlers_terminal as terminal
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO

async def main():
    db = await app.ensure_db()
    dao = AgentRunsDAO(db)
    if sys.argv[1] == "crash":
        await SessionsDAO(db).create(id="terminal-crash", workspace_path=str(Path.cwd()))
        async def emit(*args): pass
        command = f'"{sys.executable}" -c "import sys,time; print(\'out-proof\',flush=True); print(\'err-proof\',file=sys.stderr,flush=True); time.sleep(3)"'
        session = await terminal.start_terminal_command(command=command, cwd=str(Path.cwd()),
            timeout_s=10, chat_session_id="terminal-crash", emit=emit)
        for _ in range(100):
            await asyncio.sleep(.05)
            if session.run_step_id:
                step = await dao.get_step(session.run_step_id)
                if "err-proof" in (step["payload"].get("output_tail") or "") and "out-proof" in step["payload"]["output_tail"]:
                    print(json.dumps({"run": session.run_id, "step": step}), flush=True)
                    os._exit(23)
        raise AssertionError("checkpoint did not arrive")
    run = (await dao.list_runs(session_id="terminal-crash"))[0]
    print(json.dumps(await dao.snapshot(run["id"])), flush=True)
    await db.close()

try:
    asyncio.run(main())
except BaseException:
    import traceback
    traceback.print_exc()
    os._exit(1)
'''


def test_terminal_checkpoint_survives_agent_crash(tmp_path):
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]),
               MINIMAX_CODE_DATA_DIR=str(tmp_path / "data"), MINIMAX_CODE_WORKSPACE=str(tmp_path),
               MINIMAX_CODE_FORCE_MOCK="1")
    first = subprocess.run([sys.executable, "-c", CHILD, "crash"], cwd=tmp_path,
                           env=env, capture_output=True, text=True, timeout=30)
    assert first.returncode == 23, first.stderr
    before = json.loads(first.stdout.strip().splitlines()[-1])
    assert before["step"]["status"] == "running"
    second = subprocess.run([sys.executable, "-c", CHILD, "recover"], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    snapshot = json.loads(second.stdout.strip().splitlines()[-1])
    assert snapshot["run"]["status"] == "interrupted"
    step = next(s for s in snapshot["steps"] if s["id"] == before["step"]["id"])
    assert step["status"] == "failed"
    payload = step["payload"]
    assert payload["outcome"] == "unknown" and payload["requires_review"] is True
    assert payload["output_may_be_incomplete"] is True
    assert {c["stream"] for c in payload["output_chunks"]} == {"stdout", "stderr"}
    assert "out-proof" in payload["output_tail"] and "err-proof" in payload["output_tail"]
    assert len(payload["output_tail"]) <= 1200
