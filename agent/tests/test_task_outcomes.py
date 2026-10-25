"""Offline outcome truth across real RunService, validation, scheduler and SQLite."""
from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_project_validation import (  # noqa: F401
    _admit_validated_run,
    _quoted_fail_command,
    _quoted_ok_command,
    native,
)

from minimax_code.ipc import builtins
from minimax_code.ipc.handlers_tasks import register_task_handlers
from minimax_code.progress import ProgressTracker
from minimax_code.runs import get_run_service
from minimax_code.scheduler import JobScheduler
from minimax_code.storage.dao.messages import MessagesDAO
from minimax_code.storage.dao.tasks import TaskDAO, TasksDAO
from minimax_code.verification import task_outcome, verification_evidence, verify_native_result


@pytest.mark.parametrize("status", ["cancelled", "budget_paused", "interrupted", "failed"])
async def test_noncompleted_runs_never_start_validator(monkeypatch, status, tmp_path):
    validator = AsyncMock()
    monkeypatch.setattr("minimax_code.verification.run_validate_command", validator)
    evidence = await verify_native_result(status=status, mode="chat", command="must not run",
        workspace=str(tmp_path), truncated=False, base={})
    assert evidence["verdict"] == "unknown" and evidence["reason"] == f"execution_{status}"
    assert evidence["requires_review"] and not validator.called


@pytest.mark.parametrize("mode,truncated,verdict", [("plan", False, "not_applicable"), ("chat", True, "unknown")])
async def test_plan_and_truncation_skip_validation(monkeypatch, tmp_path, mode, truncated, verdict):
    validator = AsyncMock()
    monkeypatch.setattr("minimax_code.verification.run_validate_command", validator)
    evidence = await verify_native_result(status="completed", mode=mode, command="skip",
        workspace=str(tmp_path), truncated=truncated, base={})
    assert evidence["verdict"] == verdict and not validator.called


def test_verdict_preserves_tool_failures_and_existing_review():
    passed = {"exit_code": 0, "ok": True}
    failed = verification_evidence(configured=True, validation=passed, base={"failed_tool_calls": 1})
    assert failed["verdict"] == "failed" and failed["requires_review"]
    assert verification_evidence(base={"requires_review": True})["requires_review"]
    assert verification_evidence()["reason"] == "not_configured"
    assert not verification_evidence()["requires_review"]
    assert verification_evidence(engine="codex")["reason"] == "engine_validation_not_supported"


async def test_missing_validation_workspace_cannot_use_process_cwd(monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr("minimax_code.verification.run_validate_command", spawn)
    evidence = await verify_native_result(status="completed", mode="execute", command="test",
        workspace="", truncated=False, base={})
    assert evidence["verdict"] == "unknown" and evidence["requires_review"]
    assert not spawn.called


@pytest.mark.parametrize("bus", ["missing", "evicted", "broken"])
@pytest.mark.parametrize("mode", ["chat", "execute"])
async def test_actual_shell_write_validates_once_without_fs_bus(native, monkeypatch, bus, mode):  # noqa: F811
    native.shell_write = True
    if bus == "broken":
        monkeypatch.setattr("minimax_code.verification.run_touched_files", lambda *a: (_ for _ in ()).throw(RuntimeError("probe failed")))
    elif bus == "evicted":
        from minimax_code.app import ensure_fs_bus
        for n in range(600):
            ensure_fs_bus().emit("modified", [f"unrelated-{n}"], cause="test", run_id="other")
    command = f'"{sys.executable}" -c "from pathlib import Path; p=Path(\'validated\'); p.write_text(p.read_text()+\'x\' if p.exists() else \'x\')"'
    await _admit_validated_run(native, command, mode=mode)
    assert (native.root / "shell.txt").read_text() == "actual shell write"
    assert (native.root / "validated").read_text() == "x"
    ctx = SimpleNamespace(server=native.server, reply=AsyncMock(), reply_error=AsyncMock(), emit=AsyncMock())
    await builtins.handle_agent_send_message({"session_id": "ses_val", "content": "change some code", "mode": mode, "idempotency_key": "validated"}, ctx)
    assert (native.root / "validated").read_text() == "x"
    run = (await native.dao.list_runs(session_id="ses_val"))[0]
    evidence = run["metadata"]["verification"]
    assert evidence["verdict"] == "passed"
    rows = await MessagesDAO(native.db).list_for_session("ses_val")
    assert rows[-1]["metadata"]["verification"] == evidence


@pytest.mark.parametrize("failure", ["spawn", "timeout"])
async def test_validator_infrastructure_is_unknown_and_reviewed(native, monkeypatch, failure):  # noqa: F811
    if failure == "spawn":
        monkeypatch.setattr("minimax_code.verification.spawn_shell", AsyncMock(side_effect=OSError("spawn unavailable")))
    else:
        monkeypatch.setenv("MINIMAX_CODE_VALIDATE_TIMEOUT_S", ".1")
    await _admit_validated_run(native, f'"{sys.executable}" -c "import time; time.sleep(20)"')
    run = (await native.dao.list_runs(session_id="ses_val"))[0]
    assert run["status"] == "completed"
    evidence = run["metadata"]["verification"]
    assert evidence["verdict"] == "unknown" and evidence["requires_review"]
    assert evidence["reason"] == ("validation_spawn_failed" if failure == "spawn" else "validation_timeout")


async def test_cancel_during_validation_reaps_process_and_preserves_answer(native, monkeypatch):  # noqa: F811
    import minimax_code.verification as verification
    spawned = asyncio.Event()
    processes = []
    original = verification.spawn_shell
    async def capture(*args, **kwargs):
        proc = await original(*args, **kwargs)
        processes.append(proc)
        spawned.set()
        return proc
    monkeypatch.setattr(verification, "spawn_shell", capture)
    pending = asyncio.create_task(_admit_validated_run(native, f'"{sys.executable}" -c "import time; time.sleep(20)"'))
    await asyncio.wait_for(spawned.wait(), 5)
    get_run_service(native.server).cancel_run(native.fired_run_id)
    await asyncio.wait_for(pending, 5)
    run = await native.dao.get_run(native.fired_run_id)
    receipt = await native.dao.result_payload(run["id"])
    assert processes[0].returncode is not None
    assert run["status"] == "cancelled" and receipt["text"] == "done"
    assert receipt["verification"]["verdict"] == "unknown"
    assert receipt["verification"]["reason"] == "execution_cancelled"
    assert (await MessagesDAO(native.db).list_for_session("ses_val"))[-1]["metadata"]["verification"] == receipt["verification"]


async def test_final_evidence_persistence_failure_retains_saved_answer(native, monkeypatch):  # noqa: F811
    original = native.dao.__class__.update_run_status
    failed = False
    async def fail_once(self, run_id, **kwargs):
        nonlocal failed
        if kwargs.get("status") == "completed" and not failed:
            failed = True
            raise RuntimeError("final evidence write failed")
        return await original(self, run_id, **kwargs)
    monkeypatch.setattr(native.dao.__class__, "update_run_status", fail_once)
    await _admit_validated_run(native, _quoted_ok_command())
    run = (await native.dao.list_runs(session_id="ses_val"))[0]
    receipt = await native.dao.result_payload(run["id"])
    assert run["status"] == "failed" and receipt["text"] == "done"
    assert receipt["verification"]["verdict"] == "unknown" and receipt["verification"]["requires_review"]
    assert (await MessagesDAO(native.db).list_for_session("ses_val"))[-1]["content"] == "done"


@pytest.mark.parametrize("receipt,status", [({"ok": False}, "failed"),
    ({"run_status": "failed", "budget_paused": True}, "failed"),
    ({"run_status": "cancelled", "budget_paused": True, "cancelled": True}, "cancelled"),
    ({"run_id": "r", "text": "legacy"}, "requires_review"),
    ({"run_status": "completed", "ok": False, "verification": {"requires_review": True}}, "requires_review"),
    ({"run_status": "budget_paused", "ok": False}, "budget_paused")])
def test_projection_does_not_invent_success(receipt, status):
    assert task_outcome(receipt) == status


async def fire(native, monkeypatch, *, flags=None, command=None, link_failure=False):  # noqa: F811
    import minimax_code.scheduler as module
    native.result_flags = flags or {}
    await native.projects.create(id="p", name="p", root_path=str(native.root), validate_command=command or "")
    from minimax_code.model_snapshot import ModelSnapshot
    from minimax_code.storage.dao.providers import ProviderDAO
    provider = await ProviderDAO(native.db).create(name="offline",protocol="openai",base_url="https://offline.invalid")
    async def offline_model(db, context):
        from minimax_code.schedule_context import validate_context
        await validate_context(db,context)
        return ModelSnapshot(SimpleNamespace(default_model="test",mock=True,close=AsyncMock()),"test",None,10000,provider["id"],"openai")
    monkeypatch.setattr("minimax_code.schedule_context.resolve_model",offline_model)
    monkeypatch.setattr("minimax_code.ipc.handlers_permissions._ensure_permission_store",AsyncMock(return_value=SimpleNamespace(lookup=lambda _: {"action":"allow"})))
    scheduler = JobScheduler(native.db)
    monkeypatch.setattr(module, "_IPC_SERVER", native.server)
    if link_failure:
        monkeypatch.setattr(scheduler._tasks, "link_run", AsyncMock(side_effect=RuntimeError("link failed")))
    row = await scheduler.add_job(name="work", cron_expr="* * * * *", payload={"prompt": "work"}, context={"project_id":"p","provider_id":provider["id"],"model":"test","budget_tokens":10000})
    await scheduler.run_now(row["id"],idempotency_key="manual",expected_revision=1)
    async def drain():
        await asyncio.gather(*list(scheduler._inflight),return_exceptions=True)
    pending=asyncio.create_task(drain())
    for _ in range(500):
        tasks = await scheduler._tasks.list()
        if link_failure and pending.done():
            break
        if tasks and tasks[0].get("run_id") and native.fired_run_id:
            break
        await asyncio.sleep(.01)
    return scheduler, pending


@pytest.mark.parametrize("flags,command,status,verdict", [
    ({"budget_exhausted": True}, None, "budget_paused", "unknown"),
    ({"cancelled": True}, None, "cancelled", "unknown"),
    ({"truncated": True}, None, "requires_review", "unknown"),
    ({}, _quoted_fail_command(), "requires_review", "failed"),
    ({}, _quoted_ok_command(), "completed", "passed"),
    ({}, None, "completed", "unknown")])
async def test_scheduler_real_runservice_persists_truth_and_reload(native, monkeypatch, flags, command, status, verdict):  # noqa: F811
    scheduler, pending = await fire(native, monkeypatch, flags=flags, command=command)
    native.release.set()
    await asyncio.wait_for(pending, 10)
    task = (await scheduler._tasks.list())[0]
    assert task["status"] == status and task["run_id"]
    assert task["result"] == "done" and task["verification"]["verdict"] == verdict
    run = await native.dao.get_run(task["run_id"])
    assert run["status"] == task["execution_status"]
    await native.db.close()
    await native.db.connect()
    assert (await TaskDAO(native.db).get(task["id"])) == task


async def test_link_failure_cancels_and_drains_admitted_writer(native, monkeypatch):  # noqa: F811
    scheduler, pending = await fire(native, monkeypatch, link_failure=True)
    await asyncio.wait_for(pending, 5)
    task = (await scheduler._tasks.list())[0]
    runs = await native.dao.list_runs(session_id=task["session_id"])
    assert task["status"] == "cancelled" and "link failed" in task["error"]
    assert runs[0]["status"] == "cancelled"
    assert get_run_service(native.server).get_job(runs[0]["id"]) is None


async def test_linked_task_cancel_waits_for_actual_cleanup(native, monkeypatch):  # noqa: F811
    scheduler, pending = await fire(native, monkeypatch)
    task = (await scheduler._tasks.list())[0]
    register_task_handlers(native.server, tracker=ProgressTracker(TaskDAO(native.db)))
    ctx = SimpleNamespace(server=native.server, reply=AsyncMock(), reply_error=AsyncMock(), emit=AsyncMock())
    await native.server._handlers["task.cancel"]({"task_id": task["id"]}, ctx)
    ack = ctx.reply.call_args.args[0]
    assert ack["requested"] and ack["task"]["status"] == "running"
    await asyncio.wait_for(pending, 5)
    assert (await scheduler._tasks.get(task["id"]))["status"] == "cancelled"


async def test_reopened_interruption_filters_and_counts_use_authoritative_status(native, monkeypatch):  # noqa: F811
    await native.sessions.create(id="history", title="history")
    run = await native.dao.create_run(session_id="history", title="orphan")
    dao = TasksDAO(native.db)
    await dao.create(id="history-task", session_id="history", title="history", status="running")
    await dao.link_run("history-task", run["id"])
    await native.db.close()
    await native.db.connect()
    await native.dao.recover_orphans()
    assert await dao.list(status="running") == []
    assert len(await dao.list(status="interrupted")) == await dao.count(status="interrupted") == 1
    assert await TaskDAO(native.db).list(status="running") == []
    assert (await TaskDAO(native.db).list(status="interrupted"))[0]["verification"]["reason"] == "execution_interrupted"


@pytest.mark.parametrize("settle", ["failure", "cancel"])
async def test_budget_pause_final_settle_overrides_preliminary_flags(native, monkeypatch, settle):  # noqa: F811
    native.result_flags = {"budget_exhausted": True}
    original = native.dao.__class__.update_run_status
    intercepted = False
    async def intercept(self, run_id, **kwargs):
        nonlocal intercepted
        if kwargs.get("status") == "budget_paused" and not intercepted:
            intercepted = True
            if settle == "cancel":
                get_run_service(native.server).get_job(run_id).cancel_requested = True
                raise asyncio.CancelledError()
            raise RuntimeError("pause final persistence failed")
        return await original(self, run_id, **kwargs)
    monkeypatch.setattr(native.dao.__class__, "update_run_status", intercept)
    await _admit_validated_run(native, _quoted_ok_command())
    run = await native.dao.get_run(native.fired_run_id)
    receipt = await native.dao.result_payload(run["id"])
    expected = "cancelled" if settle == "cancel" else "failed"
    assert run["status"] == receipt["run_status"] == task_outcome(receipt) == expected
    assert receipt["budget_paused"] is False and receipt["text"] == "done"
    assert receipt["verification"]["reason"] == f"execution_{expected}"
    assert (await MessagesDAO(native.db).list_for_session("ses_val"))[-1]["status"] == expected


@pytest.mark.parametrize("status", ["cancelled", "budget_paused", "interrupted"])
async def test_native_status_survives_both_history_rpcs_after_reopen(native, status):  # noqa: F811
    from minimax_code.ipc.handlers_sessions import register_session_handlers
    native.result_flags = {"cancelled": status == "cancelled", "budget_exhausted": status == "budget_paused"}
    await _admit_validated_run(native, _quoted_ok_command())
    if status == "interrupted":
        await native.dao.update_run_status(native.fired_run_id, status="running")
        await native.dao.recover_orphans()
    await native.db.close()
    await native.db.connect()
    register_session_handlers(native.server, dao=native.sessions)
    for method, key in [("session.get", "recent_messages"), ("message.list", "messages")]:
        ctx = SimpleNamespace(server=native.server, reply=AsyncMock(), reply_error=AsyncMock())
        await native.server._handlers[method]({"session_id": "ses_val"}, ctx)
        rows = ctx.reply.call_args.args[0][key]
        answer = next(row for row in rows if row["role"] == "assistant")
        assert answer["status"] == answer["metadata"]["status"] == status
        assert answer["metadata"]["partial"] is True
        assert answer["metadata"]["verification"]["reason"] == f"execution_{status}"


async def test_progress_broadcast_failure_does_not_abandon_writer(native, monkeypatch):  # noqa: F811
    native.server.broadcast = AsyncMock(side_effect=RuntimeError("subscriber failed"))
    scheduler, pending = await fire(native, monkeypatch)
    native.release.set()
    await asyncio.wait_for(pending, 10)
    task = (await scheduler._tasks.list())[0]
    assert task["status"] == "completed" and task["result"] == "done"
    assert get_run_service(native.server).get_job(task["run_id"]) is None


async def test_both_history_rpcs_keep_validation_in_progress(native, monkeypatch):  # noqa: F811
    from minimax_code.ipc.handlers_sessions import register_session_handlers
    entered, release = asyncio.Event(), asyncio.Event()
    async def held_validator(*args, **kwargs):
        entered.set()
        await release.wait()
        return {"ok": True, "exit_code": 0}
    monkeypatch.setattr("minimax_code.verification.run_validate_command", held_validator)
    pending = asyncio.create_task(_admit_validated_run(native, _quoted_ok_command()))
    await asyncio.wait_for(entered.wait(), 5)
    register_session_handlers(native.server, dao=native.sessions)
    try:
        for method, key in [("session.get", "recent_messages"), ("message.list", "messages")]:
            ctx = SimpleNamespace(server=native.server, reply=AsyncMock(), reply_error=AsyncMock())
            await native.server._handlers[method]({"session_id": "ses_val"}, ctx)
            answer = next(row for row in ctx.reply.call_args.args[0][key] if row["role"] == "assistant")
            assert answer["status"] == "streaming" and answer["streaming"] is True
            assert answer["metadata"]["run_status"] == "running"
            assert answer["metadata"]["verification"]["verdict"] == "unknown"
    finally:
        release.set()
        await pending


def test_migration_035_upgrades_populated_tasks_with_indexes_triggers_and_foreign_keys(tmp_path, monkeypatch):
    import minimax_code.storage.db as db_module
    from minimax_code.storage.db import Database
    discover = db_module.discover_migrations
    monkeypatch.setattr(db_module, "discover_migrations", lambda applied: [(v, run) for v, run in discover(applied) if v < 35])
    with Database(tmp_path / "upgrade.db") as db:
        db.migrate()
        conn = db._conn
        conn.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES('s','s','now','now')")
        conn.execute("INSERT INTO tasks(id,session_id,title,status,progress,created_at,result) VALUES('t','s','old','completed',100,'now','saved old answer')")
        conn.execute("CREATE TABLE task_audit(task_id TEXT)")
        conn.execute("CREATE TRIGGER task_custom AFTER UPDATE ON tasks BEGIN INSERT INTO task_audit VALUES(NEW.id); END")
        conn.execute("CREATE INDEX task_custom_idx ON tasks(title)")
        monkeypatch.setattr(db_module, "discover_migrations", discover)
        assert db.migrate() == [35, 36, 37]
        assert tuple(conn.execute("SELECT result,run_id FROM tasks WHERE id='t'").fetchone()) == ("saved old answer", None)
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        for state in ["budget_paused", "interrupted", "requires_review"]:
            conn.execute("UPDATE tasks SET status=? WHERE id='t'", (state,))
        assert conn.execute("SELECT count(*) FROM task_audit").fetchone()[0] == 3
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='task_custom_idx'").fetchone()
        conn.execute("INSERT INTO agent_runs(id,session_id,title,status,created_at) VALUES('r','s','r','completed','now')")
        conn.execute("UPDATE tasks SET run_id='r' WHERE id='t'")
        conn.execute("DELETE FROM agent_runs WHERE id='r'")
        assert conn.execute("SELECT run_id FROM tasks").fetchone()[0] is None
        conn.execute("DELETE FROM sessions WHERE id='s'")
        assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
