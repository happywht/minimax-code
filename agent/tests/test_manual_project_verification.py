from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_project_validation import native  # noqa: F401

from minimax_code.ipc.handlers_runs import register_run_handlers
from minimax_code.runs import get_run_service
from minimax_code.storage.dao.text_artifacts import ArtifactDAO


@pytest.fixture
async def setup(native, monkeypatch):  # noqa: F811
    from minimax_code.ipc import host_session_gate

    command = f'"{sys.executable}" -c "print(12345)"'
    await native.projects.create(id="p", name="project", root_path=str(native.root), validate_command=command)
    await native.sessions.create(id="s", project_id="p")
    source = await native.dao.create_run(session_id="s", mode="execute", status="completed", metadata={
        "project_id": "p", "workspace_snapshot": str(native.root), "engine": "native"})
    snapshot = await native.dao.snapshot(source["id"])
    info = native.root.stat()
    params = {"source_run_id": source["id"], "session_id": "s", "project_id": "p",
              "expected_service_id": await ArtifactDAO(native.db).service_id(),
              "expected_workspace": str(native.root), "expected_workspace_identity": f"{info.st_dev}:{info.st_ino}",
              "expected_command": command, "expected_cursor": snapshot["cursor"],
              "idempotency_key": "verify-key", "confirmed": True}
    rules = {"exec_command": "allow", "project_validation": "allow"}
    permissions = SimpleNamespace(lookup=lambda name: {"action": rules[name]} if name in rules else None)
    monkeypatch.setattr("minimax_code.ipc.handlers_permissions._ensure_permission_store", AsyncMock(return_value=permissions))
    monkeypatch.setattr(host_session_gate, "require_host_session", AsyncMock())
    register_run_handlers(native.server)
    return native, params, rules


def ctx(server):
    return SimpleNamespace(server=server, reply=AsyncMock(), reply_error=AsyncMock(), emit=AsyncMock())


async def submit(state, params):
    context = ctx(state.server)
    await state.server._handlers["run.verify_project"](params, context)
    return context


async def test_validation_real_command_records_separate_evidence_and_is_idempotent(setup):
    state, params, _ = setup
    before = await state.dao.snapshot(params["source_run_id"])
    response = await submit(state, params)
    response.reply_error.assert_not_awaited()
    run = response.reply.call_args.args[0]["run"]
    job = await get_run_service(state.server).job_for_run(run["id"])
    if job:
        await get_run_service(state.server).wait(job)
    result = await state.dao.result_payload(run["id"])
    assert result["source_run_id"] == params["source_run_id"]
    assert result["verification"]["verdict"] == "passed"
    assert "12345" in result["verification"]["validation"]["stdout"]
    assert await state.dao.snapshot(params["source_run_id"]) == before
    retry = await submit(state, params)
    assert retry.reply.call_args.args[0]["run"]["id"] == run["id"]
    assert retry.reply.call_args.args[0]["created"] is False


async def test_failed_command_is_not_reported_as_acceptance(setup):
    state, params, _ = setup
    command = f'"{sys.executable}" -c "raise SystemExit(3)"'
    await state.projects.update("p", validate_command=command)
    params["expected_command"] = command
    response = await submit(state, params)
    run = response.reply.call_args.args[0]["run"]
    job = await get_run_service(state.server).job_for_run(run["id"])
    if job:
        await get_run_service(state.server).wait(job)
    result = await state.dao.result_payload(run["id"])
    assert result["verification"]["verdict"] == "failed"
    assert result["verification"]["validation"]["exit_code"] == 3
    assert (await state.dao.get_run(run["id"]))["status"] == "completed"


async def test_binding_change_during_validation_keeps_output_but_verdict_unknown(setup, monkeypatch):
    from minimax_code.ipc import run_manual_verification

    state, params, _ = setup
    entered, release = asyncio.Event(), asyncio.Event()
    async def controlled(command, **kwargs):
        entered.set()
        await release.wait()
        return {"command": command, "ok": True, "exit_code": 0, "timed_out": False, "stdout": "observed"}
    monkeypatch.setattr(run_manual_verification, "run_validate_command", controlled)
    response = await submit(state, params)
    run = response.reply.call_args.args[0]["run"]
    job = await get_run_service(state.server).job_for_run(run["id"])
    await asyncio.wait_for(entered.wait(), 5)
    await state.projects.update("p", validate_command="changed")
    release.set()
    await get_run_service(state.server).wait(job)
    evidence = (await state.dao.result_payload(run["id"]))["verification"]
    assert evidence["verdict"] == "unknown" and evidence["reason"] == "validation_binding_changed"
    assert evidence["validation"]["stdout"] == "observed"


@pytest.mark.parametrize("change", ["command", "identity", "cursor", "service", "plan", "denied"])
async def test_validation_rejects_drift_plan_and_denied_rules(setup, change):
    state, params, rules = setup
    if change == "command":
        await state.projects.update("p", validate_command="different")
    elif change == "identity":
        params["expected_workspace_identity"] = "0:0"
    elif change == "cursor":
        params["expected_cursor"] += 1
    elif change == "service":
        params["expected_service_id"] = "other"
    elif change == "plan":
        await state.db.execute("UPDATE agent_runs SET mode='plan' WHERE id=?", (params["source_run_id"],))
    else:
        rules["exec_command"] = "deny"
    result = await submit(state, params)
    result.reply_error.assert_awaited_once()
    assert len(await state.dao.list_runs(session_id="s")) == 1


async def test_validation_asks_then_rechecks_changed_command(setup):
    state, params, rules = setup
    rules["exec_command"] = "ask"
    response = await submit(state, params)
    run = response.reply.call_args.args[0]["run"]
    job = await get_run_service(state.server).job_for_run(run["id"])
    for _ in range(200):
        gater = getattr(state.server, "_permission_gaters", {}).get("s")
        if gater and gater.pending_ids():
            break
        await asyncio.sleep(.01)
    else:
        pytest.fail("Permission request did not appear")
    await state.projects.update("p", validate_command="changed")
    assert gater.resolve(gater.pending_ids()[0], True)
    with pytest.raises(ValueError, match="changed"):
        await get_run_service(state.server).wait(job)
    saved = await state.dao.get_run(run["id"])
    result = await state.dao.result_payload(run["id"])
    assert saved["status"] == "failed"
    assert result["verification"]["verdict"] == "unknown"
    assert "changed" in saved["error"]


async def test_validation_waits_for_approval_before_running(setup):
    state, params, rules = setup
    rules["exec_command"] = "ask"
    response = await submit(state, params)
    run = response.reply.call_args.args[0]["run"]
    job = await get_run_service(state.server).job_for_run(run["id"])
    for _ in range(200):
        gater = getattr(state.server, "_permission_gaters", {}).get("s")
        if gater and gater.pending_ids():
            break
        await asyncio.sleep(.01)
    else:
        pytest.fail("Permission request did not appear")
    assert (await state.dao.get_run(run["id"]))["status"] == "awaiting_approval"
    assert await state.dao.result_payload(run["id"]) is None
    assert gater.resolve(gater.pending_ids()[0], True)
    await get_run_service(state.server).wait(job)
    assert (await state.dao.result_payload(run["id"]))["verification"]["verdict"] == "passed"


async def test_cancellation_during_approval_never_runs_command(setup, monkeypatch):
    state, params, rules = setup
    rules["exec_command"] = "ask"
    command = AsyncMock()
    monkeypatch.setattr("minimax_code.ipc.run_manual_verification.run_validate_command", command)
    response = await submit(state, params)
    run = response.reply.call_args.args[0]["run"]
    service = get_run_service(state.server)
    job = await service.job_for_run(run["id"])
    for _ in range(200):
        gater = getattr(state.server, "_permission_gaters", {}).get("s")
        if gater and gater.pending_ids():
            break
        await asyncio.sleep(.01)
    else:
        pytest.fail("Permission request did not appear")
    assert await service.cancel_run_durable(run["id"])
    with pytest.raises(asyncio.CancelledError):
        await service.wait(job)
    assert (await state.dao.get_run(run["id"]))["status"] == "interrupted"
    assert (await state.dao.result_payload(run["id"]))["verification"]["verdict"] == "unknown"
    command.assert_not_awaited()


async def test_denied_approval_never_runs_command(setup, monkeypatch):
    state, params, rules = setup
    rules["exec_command"] = "ask"
    command = AsyncMock()
    monkeypatch.setattr("minimax_code.ipc.run_manual_verification.run_validate_command", command)
    response = await submit(state, params)
    run = response.reply.call_args.args[0]["run"]
    service = get_run_service(state.server)
    job = await service.job_for_run(run["id"])
    for _ in range(200):
        gater = getattr(state.server, "_permission_gaters", {}).get("s")
        if gater and gater.pending_ids():
            break
        await asyncio.sleep(.01)
    else:
        pytest.fail("Permission request did not appear")
    assert gater.resolve(gater.pending_ids()[0], False)
    with pytest.raises(ValueError, match="denied"):
        await service.wait(job)
    assert (await state.dao.get_run(run["id"]))["status"] == "failed"
    assert (await state.dao.result_payload(run["id"]))["verification"]["verdict"] == "unknown"
    command.assert_not_awaited()
