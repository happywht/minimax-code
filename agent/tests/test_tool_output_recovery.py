from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_run_service import Context, native  # noqa: F401

from minimax_code.agent.core import _spool_large_output
from minimax_code.agent.tools.base import ToolResult
from minimax_code.agent.tools.tool_output import ReadToolOutput
from minimax_code.budget_ledger import budget_scope
from minimax_code.ipc.handlers_data import dump_database
from minimax_code.ipc.handlers_runs import register_run_handlers


async def test_captured_output_is_run_scoped_paged_and_exported(native, monkeypatch, tmp_path):  # noqa: F811
    from minimax_code import app

    monkeypatch.setattr(app, "ensure_db", AsyncMock(return_value=native.db))
    run = await native.dao.create_run(session_id="one", status="running", metadata={"project_id": "inbox"})
    payload = {"stdout": "中文" * 6000, "stderr": "", "exit_code": 2, "truncated_stdout": True}
    with budget_scope(SimpleNamespace(run_id=run["id"])):
        saved = await _spool_large_output(ToolResult.fail("failed", output=payload, truncated_stdout=True),
            1000, session_id="one", tool_name="exec_command", tool_call_id="call")
        assert saved and saved.metadata["truncated_stdout"] is True
        ref = saved.metadata["tool_output"]
        assert len(str(saved.output)) < 3000
        first = await ReadToolOutput().run(ref["output_ref"], offset=0, limit=1000)
        assert first.success and first.output["next_offset"] == 1000
        assert len(first.output["page"]) == 1000
    register_run_handlers(native.server)
    handler = native.server._handlers["run.output_page"]
    params = {"run_id": run["id"], "session_id": "one", "output_ref": ref["output_ref"],
              "expected_service_id": ref["service_id"], "offset": 1000, "limit": 1000}
    ctx = Context(native.server)
    await handler(params, ctx)
    assert ctx.reply.await_args.args[0]["offset"] == 1000
    for wrong in ({**params, "session_id": "two"}, {**params, "expected_service_id": "other"}):
        ctx = Context(native.server)
        await handler(wrong, ctx)
        assert ctx.reply_error.await_count == 1
    exported = await dump_database(native.db)
    assert any(row["id"] == ref["output_ref"] for row in exported["tables"]["agent_run_steps"])
    assert not (await native.dao.snapshot(run["id"]))["steps"]
    from minimax_code.bundle import _inventory, create_bundle, verify_bundle
    vault = tmp_path / "vault"
    vault.mkdir()
    target = sqlite3.connect(str(vault / "data.db"))
    try:
        await native.db.backup(target)
    finally:
        target.close()
    assert _inventory(vault / "data.db")["schema_version"] == 49
    bundle = tmp_path / "vault.zip"
    assert create_bundle(vault, bundle)["schema_version"] == 49
    assert verify_bundle(bundle)["schema_version"] == 49
    await native.db.execute("DELETE FROM agent_runs WHERE id=?", (run["id"],))
    assert not await native.db.fetchone("SELECT id FROM agent_run_steps WHERE id=?", (ref["output_ref"],))


async def test_native_loop_gives_model_bounded_reference(native, monkeypatch):  # noqa: F811
    from test_m3_context import ScriptedLLM, _HugeEchoTool, _tool_call

    from minimax_code import app
    from minimax_code.agent.core import AgentConfig, AgentCore
    from minimax_code.agent.tools import ToolRegistry
    from minimax_code.agent.types import StreamChunk

    monkeypatch.setattr(app, "ensure_db", AsyncMock(return_value=native.db))
    run = await native.dao.create_run(session_id="one", status="running", metadata={"project_id": "inbox"})
    llm = ScriptedLLM([[StreamChunk(tool_call_deltas=[_tool_call("echo_huge", {"filler": "y"})], finish_reason="tool_calls")],
                       [StreamChunk(delta="done", finish_reason="stop")]])
    registry = ToolRegistry()
    registry.register(_HugeEchoTool())
    core = AgentCore(llm=llm, registry=registry, config=AgentConfig(max_tool_output_bytes=2000))
    with budget_scope(SimpleNamespace(run_id=run["id"])):
        assert (await core.run(session_id="one", user_message="go")).final_text == "done"
    observed = str(llm.messages)
    assert "read_tool_output" in observed and "output_" in observed and "x" * 120000 not in observed
