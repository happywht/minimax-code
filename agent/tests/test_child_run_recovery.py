from __future__ import annotations

from unittest.mock import AsyncMock

from test_run_service import Context, native  # noqa: F401

from minimax_code.ipc.handlers_runs import register_run_handlers


async def test_child_list_scopes_parent_session_and_preserves_unconfirmed_status(native, monkeypatch):  # noqa: F811
    from minimax_code import app

    monkeypatch.setattr(app, "ensure_db", AsyncMock(return_value=native.db))
    parent = await native.dao.create_run(session_id="one", status="completed", metadata={"project_id": "inbox"})
    other = await native.dao.create_run(session_id="two", status="completed", metadata={"project_id": "inbox"})
    await native.dao.create_run(id="child", session_id="two", mode="subagent", status="running",
        metadata={"parent_session_id": "one", "parent_run_id": parent["id"], "agent_name": "Worker"})
    await native.dao.create_run(id="foreign", session_id="two", mode="subagent", status="completed",
        metadata={"parent_session_id": "two", "parent_run_id": other["id"]})
    await native.dao.create_run(id="legacy", session_id="two", mode="subagent", status="completed",
        metadata={"parent_session_id": "one"})
    register_run_handlers(native.server)
    handler = native.server._handlers["run.child_list"]
    ctx = Context(native.server)
    await handler({"session_id": "one"}, ctx)
    result = ctx.reply.await_args.args[0]
    assert result["session_id"] == "one" and result["project_id"] == "inbox" and result["service_id"]
    assert [item["run_id"] for item in result["children"]] == ["child"]
    assert result["children"][0]["status"] == "running"
    assert result["children"][0]["live"] is False
    await native.db.execute("UPDATE sessions SET project_id='other' WHERE id='one'")
    changed = Context(native.server)
    await handler({"session_id": "one"}, changed)
    assert changed.reply.await_args.args[0]["children"] == []
