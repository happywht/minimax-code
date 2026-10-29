from __future__ import annotations

from unittest.mock import AsyncMock

from test_git_handlers import repo  # noqa: F401
from test_run_service import Context, native  # noqa: F401
from test_run_workspace_diff import scoped  # noqa: F401

from minimax_code.run_file_changes import capture, save_comparison
from minimax_code.runs import RunJob, RunService


async def test_bounded_samples_report_added_modified_deleted(scoped, monkeypatch):  # noqa: F811
    state, repository, root, run = scoped
    monkeypatch.setattr("minimax_code.ipc.host_session_gate.require_host_session", AsyncMock())
    job = RunJob("one", run_id=run["id"], dao=state.dao, workspace=str(root), project_id="proj")
    original = root / "helper.py"
    original.write_text("before")
    (root / "old.txt").write_text("old")
    before = await capture(job)
    assert before is not None
    original.write_text("after")
    (root / "old.txt").unlink()
    (root / "new.txt").write_text("new secret contents")
    (repository / "outside.txt").write_text("outside")
    await save_comparison(job, before)
    snapshot = await state.dao.snapshot(run["id"])
    payload = snapshot["steps"][-1]["payload"]
    assert payload == {"source": "run_file_changes", "available": True, "added": ["new.txt"],
                       "deleted": ["old.txt"], "modified": ["helper.py"]}
    assert "new secret contents" not in str(payload)


async def test_limit_and_cancel_report_unavailable(scoped, monkeypatch):  # noqa: F811
    state, _, root, run = scoped
    monkeypatch.setattr("minimax_code.ipc.host_session_gate.require_host_session", AsyncMock())
    monkeypatch.setattr("minimax_code.run_file_changes.MAX_FILES", 0)
    job = RunJob("one", run_id=run["id"], dao=state.dao, workspace=str(root), project_id="proj")
    assert await capture(job) is None
    await save_comparison(job, None)
    assert (await state.dao.snapshot(run["id"]))["steps"][-1]["payload"]["available"] is False


async def test_service_captures_around_operation(scoped, monkeypatch):  # noqa: F811
    state, _, root, run = scoped
    monkeypatch.setattr("minimax_code.ipc.host_session_gate.require_host_session", AsyncMock())
    job = RunJob("one", run_id=run["id"], dao=state.dao, workspace=str(root),
                 project_id="proj", capture_file_changes=True)
    async def operation(_job):
        (root / "during.txt").write_text("inside task")
        return "ok"
    service = RunService()
    service.submit("one", operation, job=job)
    assert await service.wait(job) == "ok"
    payload = (await state.dao.snapshot(run["id"]))["steps"][-1]["payload"]
    assert payload["available"] and payload["added"] == ["during.txt"]


async def test_native_admission_enables_capture(scoped, monkeypatch):  # noqa: F811
    from minimax_code.ipc.builtins import handle_agent_send_message
    from minimax_code.runs import get_run_service

    state, _, root, _ = scoped
    monkeypatch.setattr("minimax_code.ipc.host_session_gate.require_host_session", AsyncMock())
    async def change():
        (root / "generated.txt").write_text("new")
    state.tool = change
    state.release.set()
    ctx = Context(state.server)
    await handle_agent_send_message({"session_id": "one", "content": "work",
                                     "capture_file_changes": True}, ctx, wait=False)
    run = ctx.reply.await_args.args[0]["run"]
    job = get_run_service(state.server).get_job(run["id"])
    await get_run_service(state.server).wait(job)
    steps = (await state.dao.snapshot(run["id"]))["steps"]
    evidence = next(step["payload"] for step in steps if step["payload"].get("source") == "run_file_changes")
    assert evidence["available"] and evidence["added"] == ["generated.txt"]
