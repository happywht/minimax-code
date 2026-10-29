from __future__ import annotations

from unittest.mock import AsyncMock

from test_run_service import Context, native  # noqa: F401

from minimax_code.ipc.handlers_runs import register_run_handlers
from minimax_code.workspace.child import prepare_child


async def test_review_is_bound_and_collect_requires_current_revision(native, tmp_path, monkeypatch):  # noqa: F811
    from minimax_code import app, workspace_ctx

    monkeypatch.setattr(app, "ensure_db", AsyncMock(return_value=native.db))
    monkeypatch.setattr(workspace_ctx, "resolve_root_for_session", AsyncMock(return_value=tmp_path))
    (tmp_path / "a.txt").write_text("before", encoding="utf-8")
    parent = await native.dao.create_run(session_id="one", status="completed", metadata={
        "project_id": "inbox", "workspace_snapshot": str(tmp_path)})
    child_run = await native.dao.create_run(id="child_review", session_id="two", mode="subagent", status="completed",
        metadata={"parent_session_id": "one", "parent_run_id": parent["id"]})
    child = await prepare_child(tmp_path, child_run["id"])
    (child.root / "a.txt").write_text("after", encoding="utf-8")
    child.finish("completed")
    register_run_handlers(native.server)
    handler = native.server._handlers["run.child_workspace"]
    request = {"session_id": "one", "run_id": child_run["id"], "action": "preview"}
    ctx = Context(native.server)
    await handler(request, ctx)
    assert ctx.reply.await_args is not None, ctx.reply_error.await_args
    result = ctx.reply.await_args.args[0]
    assert result["files"][0]["path"] == "a.txt"
    wrong = Context(native.server)
    await handler({**request, "session_id": "two"}, wrong)
    assert wrong.reply_error.await_count == 1
    collect = {**request, "action": "collect", "revision": result["revision"],
               "expected_service_id": result["service_id"]}
    (tmp_path / "a.txt").write_text("user", encoding="utf-8")
    stale = Context(native.server)
    await handler(collect, stale)
    assert stale.reply_error.await_count == 1
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "user"
    (tmp_path / "a.txt").write_text("before", encoding="utf-8")
    done = Context(native.server)
    await handler(collect, done)
    assert done.reply.await_args.args[0]["report"]["complete"]
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == "after"
