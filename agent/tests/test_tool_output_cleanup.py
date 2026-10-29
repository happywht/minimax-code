from __future__ import annotations

from unittest.mock import AsyncMock

from test_run_service import Context, native  # noqa: F401

from minimax_code.ipc.handlers_runs import register_run_handlers
from minimax_code.tool_outputs import read, save


async def test_explicit_scoped_cleanup_preserves_run_and_marks_output(native, monkeypatch):  # noqa: F811
    from minimax_code import app

    monkeypatch.setattr(app, "ensure_db", AsyncMock(return_value=native.db))
    run = await native.dao.create_run(session_id="one", status="running", metadata={"project_id": "inbox"})
    other = await native.dao.create_run(session_id="two", status="running", metadata={"project_id": "inbox"})
    first = await save(native.db, run["id"], "one", "call", "exec_command", {"stdout": "hello"})
    second = await save(native.db, other["id"], "two", "call", "exec_command", {"stdout": "private"})
    step = await native.dao.create_step(run_id=run["id"], session_id="one", kind="observation", summary="summary remains")
    register_run_handlers(native.server)
    listing = native.server._handlers["run.output_inventory"]
    clear = native.server._handlers["run.output_clear"]
    params = {"session_id": "one", "run_id": run["id"], "output_ref": first["output_ref"],
              "expected_service_id": first["service_id"], "expected_sha256": first["sha256"]}
    ctx = Context(native.server)
    await listing({"session_id": "one"}, ctx)
    before = ctx.reply.await_args.args[0]
    assert [item["output_ref"] for item in before["items"]] == [first["output_ref"]]
    assert before["total_bytes"] == first["bytes"] + second["bytes"]
    for wrong in (params, {**params, "session_id": "two"}, {**params, "expected_sha256": "0" * 64},
                  {**params, "expected_service_id": "other"}):
        rejected = Context(native.server)
        await clear(wrong, rejected)
        assert rejected.reply_error.await_count == 1
    await native.dao.update_run_status(run["id"], status="completed")
    done = Context(native.server)
    await clear(params, done)
    assert done.reply.await_args.args[0]["already_cleared"] is False
    assert done.emit.await_args.args[0] == "run.output_cleared"
    again = Context(native.server)
    await clear(params, again)
    assert again.reply.await_args.args[0]["already_cleared"] is True
    after = Context(native.server)
    await listing({"session_id": "one"}, after)
    item = after.reply.await_args.args[0]["items"][0]
    assert item["cleared_at"] and after.reply.await_args.args[0]["total_bytes"] == second["bytes"]
    import pytest
    with pytest.raises(ValueError, match="explicitly cleared"):
        await read(native.db, run_id=run["id"], session_id="one", ref=first["output_ref"], service_id=first["service_id"])
    assert (await native.dao.get_step(step["id"]))["summary"] == "summary remains"
    assert await native.dao.get_run(run["id"])
