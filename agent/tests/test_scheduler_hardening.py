"""v1.2.2 regression tests — scheduler fire-path hardening.

Four scheduler defects pinned here:

1. **Private-loop payload execution** — ``_run_payload_sync`` used
   ``asyncio.run`` inside the executor thread, so any main-loop-bound
   resource the payload touched (aiosqlite connections, locks) blew up
   with "attached to a different loop". The payload now runs on the
   agent's main loop via ``run_coroutine_threadsafe``.
2. **Silent fire-and-forget** — bare ``loop.create_task(_fire(...))``
   results were never referenced: the loop could garbage-collect a
   task mid-run and exceptions died unseen. ``_spawn_fire`` keeps a
   strong reference and logs crashes.
3. **Unguarded bookkeeping** — a single failed write in ``_fire``'s
   success path (e.g. SQLITE_BUSY) aborted the remaining updates,
   leaving a stale ``running`` task row and a missing ``last_run_at``.
   Each step is now individually guarded.
4. **Hung payload safety valve** — a payload that never returned held
   an executor worker forever; ``_PAYLOAD_TIMEOUT_S`` now cancels it.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

import minimax_code.scheduler as scheduler_module
from minimax_code.scheduler import JobScheduler, _run_payload_sync
from minimax_code.storage.dao.scheduled_jobs import ScheduledJobsDAO
from minimax_code.storage.dao.tasks import TasksDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return make_temp_database_path(tmp_path)


@pytest.fixture
async def async_db(db_path: Path) -> AsyncDatabase:
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# 1. Payload runs on the main loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payload_runs_on_main_loop(async_db: AsyncDatabase) -> None:
    """The payload coroutine must run on the agent's main loop, not a
    private one — main-loop-bound resources (here: the shared aiosqlite
    connection) must survive the executor hop."""
    loop = asyncio.get_running_loop()
    seen: dict[str, Any] = {}

    async def runner(payload: dict[str, Any]) -> dict[str, Any]:
        seen["loop"] = asyncio.get_running_loop()
        # Real main-loop-bound resource: the shared aiosqlite conn.
        row = await async_db.fetchone("SELECT 1")
        seen["one"] = row[0] if row is not None else None
        return {"ok": True}

    result = await loop.run_in_executor(None, _run_payload_sync, runner, {}, loop)

    assert result == {"ok": True}
    assert seen["loop"] is loop, "payload ran on a private loop"
    assert seen["one"] == 1


@pytest.mark.asyncio
async def test_run_payload_sync_falls_back_without_loop() -> None:
    """No main loop passed + no running loop in the worker thread
    (direct sync call): the private-loop fallback keeps working."""

    async def runner(payload: dict[str, Any]) -> str:
        return "echo"

    result = await asyncio.to_thread(_run_payload_sync, runner, {"k": 1})
    assert result == "echo"


# ---------------------------------------------------------------------------
# 2. Tracked fire-and-forget
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_fire_keeps_reference_then_discards(
    async_db: AsyncDatabase,
) -> None:
    """The in-flight task must be strongly referenced while running
    (loop GC would otherwise kill it mid-run) and discarded on done."""
    scheduler = JobScheduler(async_db)
    started = asyncio.Event()

    async def slow_fire(row: Any, *, source: str) -> str:
        started.set()
        await asyncio.sleep(0.05)
        return "done"

    scheduler._fire = slow_fire  # type: ignore[method-assign]
    scheduler._spawn_fire({"id": "job_ref"}, source="manual")

    await asyncio.wait_for(started.wait(), timeout=1)
    assert len(scheduler._inflight) == 1, "strong reference missing mid-run"

    await asyncio.gather(*list(scheduler._inflight))
    assert len(scheduler._inflight) == 0, "reference leaked after completion"


@pytest.mark.asyncio
async def test_spawn_fire_logs_crash(
    async_db: AsyncDatabase, caplog: pytest.LogCaptureFixture
) -> None:
    """A crashing ``_fire`` must surface in the logs — before v1.2.2
    the exception vanished with the unreferenced task."""
    scheduler = JobScheduler(async_db)

    async def boom(row: Any, *, source: str) -> None:
        raise RuntimeError("tick-boom")

    scheduler._fire = boom  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger="minimax_code.scheduler"):
        scheduler._spawn_fire({"id": "job_boom"}, source="cron")
        await asyncio.gather(*list(scheduler._inflight), return_exceptions=True)

    assert len(scheduler._inflight) == 0
    assert any(
        "job_boom" in r.getMessage() for r in caplog.records
    ), f"crash not logged: {caplog.text!r}"


# ---------------------------------------------------------------------------
# 3. Guarded bookkeeping in _fire
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fire_survives_bookkeeping_failure(async_db: AsyncDatabase) -> None:
    """A failing ``update_status(completed)`` in the success path must
    not abort ``record_run`` — each bookkeeping step is guarded."""
    scheduler = JobScheduler(async_db)
    record_calls: list[str] = []

    class _FailingTasks(TasksDAO):
        async def update_status(self, task_id: str, **kwargs: Any) -> Any:
            if kwargs.get("status") == "completed":
                raise RuntimeError("simulated SQLITE_BUSY")
            return await super().update_status(task_id, **kwargs)

    class _RecordingJobs(ScheduledJobsDAO):
        async def record_run(self, job_id: str, **kwargs: Any) -> Any:
            record_calls.append(job_id)
            return await super().record_run(job_id, **kwargs)

    scheduler._tasks = _FailingTasks(async_db)
    scheduler._dao = _RecordingJobs(async_db)

    row = {
        "id": "job_guard",
        "name": "guarded",
        "cron_expr": "* * * * *",
        "payload": {},
    }
    # Must not raise despite update_status blowing up mid-bookkeeping.
    await scheduler._fire(row, source="manual")

    assert record_calls == ["job_guard"], "record_run was skipped by the failure"


# ---------------------------------------------------------------------------
# 4. Hung-payload safety valve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_payload_timeout_cancels_hung_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung payload is cancelled after the safety-valve timeout
    instead of holding the executor worker forever."""
    monkeypatch.setattr(scheduler_module, "_PAYLOAD_TIMEOUT_S", 0.05)
    cancelled = asyncio.Event()

    async def hung(payload: dict[str, Any]) -> str:
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return "never"  # pragma: no cover — unreachable

    loop = asyncio.get_running_loop()
    with pytest.raises(TimeoutError):
        await loop.run_in_executor(None, _run_payload_sync, hung, {}, loop)

    await asyncio.wait_for(cancelled.wait(), timeout=1)
