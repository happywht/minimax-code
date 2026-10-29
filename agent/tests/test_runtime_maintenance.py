"""Maintenance joins real scheduled runs but never asserts global readiness."""
from __future__ import annotations

import asyncio

import pytest
from test_project_validation import _Ctx, native  # noqa: F401
from test_schedule_occurrences import create, scheduled, wait_until  # noqa: F401

from minimax_code.ipc.handlers_scheduled import register_scheduled_handlers
from minimax_code.runtime.maintenance import MaintenanceIncomplete, drain_for_maintenance


async def test_maintenance_waits_for_handler_run_and_retains_cancelled_waiter(
    scheduled, monkeypatch  # noqa: F811
):
    from minimax_code import app, scheduler
    from minimax_code.ipc import handlers_terminal

    state = scheduled
    monkeypatch.setattr(scheduler, "_SCHEDULER", state.scheduler)
    monkeypatch.setattr(scheduler, "_SCHEDULER_QUIESCING", False)
    monkeypatch.setattr(app, "_CODEBASE_INDEX_CLOSING", False)
    monkeypatch.setattr(app, "_CODEBASE_INDEXERS", {})
    monkeypatch.setattr(app, "_CODEBASE_INDEXER", None)
    monkeypatch.setattr(app, "_CODEBASE_AUTO_TASKS", set())
    monkeypatch.setattr(handlers_terminal, "_SESSIONS", {})
    monkeypatch.setattr(handlers_terminal, "_TERMINAL_CLOSING", False)
    monkeypatch.setattr(handlers_terminal, "_TERMINAL_DRAIN_TASK", None)
    job = await create(state)
    register_scheduled_handlers(state.server, state.scheduler)
    ctx = _Ctx(state.server)
    await state.server._handlers["schedule.run_now"](
        {"job_id": job["id"], "idempotency_key": "existing", "expected_revision": 1}, ctx
    )
    ctx.reply.assert_awaited_once()
    ack = ctx.reply.await_args.args[0]
    await wait_until(lambda: _value(bool(state.models)))
    waiter = asyncio.create_task(drain_for_maintenance(state.server))
    await wait_until(lambda: _value(state.scheduler._stopping))
    second = _Ctx(state.server)
    await state.server._handlers["schedule.run_now"](
        {"job_id": job["id"], "idempotency_key": "new", "expected_revision": 1}, second
    )
    second.reply_error.assert_awaited_once()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    other = asyncio.create_task(drain_for_maintenance(state.server))
    await asyncio.sleep(0)
    assert not other.done()
    state.release.set()
    result = await other
    assert not result.upgrade_ready
    with pytest.raises(MaintenanceIncomplete, match="Global upgrade readiness"):
        result.require_upgrade_ready()
    occurrence = await state.scheduler._occurrences.get(ack["occurrence_id"])
    assert occurrence["status"] == "completed"
    assert await drain_for_maintenance(state.server) is result


async def _value(value):
    return value


async def test_drain_failure_is_retained_and_fails_closed(monkeypatch):
    from types import SimpleNamespace

    from minimax_code import app, scheduler
    from minimax_code.ipc import handlers_terminal
    from minimax_code.runs import RunService

    monkeypatch.setattr(app, "_CODEBASE_INDEX_CLOSING", False)
    monkeypatch.setattr(app, "_CODEBASE_INDEXERS", {})
    monkeypatch.setattr(app, "_CODEBASE_INDEXER", None)
    monkeypatch.setattr(app, "_CODEBASE_AUTO_TASKS", set())
    monkeypatch.setattr(scheduler, "_SCHEDULER", None)
    monkeypatch.setattr(scheduler, "_SCHEDULER_QUIESCING", False)

    async def fail():
        raise RuntimeError("terminal cleanup unconfirmed")
    monkeypatch.setattr(handlers_terminal, "drain_terminal_sessions", fail)
    server = SimpleNamespace(_run_service=RunService())
    for _ in range(2):
        with pytest.raises(MaintenanceIncomplete, match="producer drains failed") as error:
            await drain_for_maintenance(server)
        assert "terminal cleanup unconfirmed" in str(error.value.__cause__)

async def test_existing_run_can_start_terminal_before_terminal_fence(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    from minimax_code import app, scheduler
    from minimax_code.ipc import handlers_terminal
    from minimax_code.runs import RunService

    monkeypatch.setattr(app, "_CODEBASE_INDEX_CLOSING", False)
    monkeypatch.setattr(app, "_CODEBASE_INDEXERS", {})
    monkeypatch.setattr(app, "_CODEBASE_INDEXER", None)
    monkeypatch.setattr(app, "_CODEBASE_AUTO_TASKS", set())
    monkeypatch.setattr(scheduler, "_SCHEDULER", None)
    monkeypatch.setattr(scheduler, "_SCHEDULER_QUIESCING", False)
    monkeypatch.setattr(handlers_terminal, "_SESSIONS", {})
    monkeypatch.setattr(handlers_terminal, "_TERMINAL_CLOSING", False)
    monkeypatch.setattr(handlers_terminal, "_TERMINAL_DRAIN_TASK", None)
    service = RunService()
    server = SimpleNamespace(_run_service=service)
    entered, release = asyncio.Event(), asyncio.Event()

    async def emit(*args):
        pass

    async def operation(job):
        entered.set()
        await release.wait()
        terminal = await handlers_terminal.start_terminal_command(
            command=f'"{sys.executable}" -c "print(123)"', cwd=str(tmp_path),
            timeout_s=10, chat_session_id=None, emit=emit,
        )
        await terminal.task
        assert terminal.status == "completed"

    service.submit("existing", operation)
    await entered.wait()
    drain = asyncio.create_task(drain_for_maintenance(server))
    await wait_until(lambda: _value(service._closing))
    assert not handlers_terminal._TERMINAL_CLOSING
    release.set()
    result = await drain
    assert "local terminal tasks and main processes" in result.drained
    assert handlers_terminal._TERMINAL_CLOSING
    assert not result.upgrade_ready
