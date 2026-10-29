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
from test_project_validation import native  # noqa: F401
from test_schedule_occurrences import create, scheduled, settled, wait_until  # noqa: F401

import minimax_code.scheduler as scheduler_module
from minimax_code.scheduler import JobScheduler, _run_payload_sync
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
async def test_fire_survives_bookkeeping_failure(scheduled, monkeypatch) -> None:  # noqa: F811
    """A task projection failure preserves the authoritative run and blocks recurrence."""
    state=scheduled
    original=state.scheduler._occurrences.settle
    async def fail_completed(oid,status,**kwargs):
        if status=="completed":
            raise RuntimeError("simulated SQLITE_BUSY")
        return await original(oid,status,**kwargs)
    monkeypatch.setattr(state.scheduler._occurrences,"settle",fail_completed)
    job=await create(state)
    ack=await state.scheduler.run_now(job["id"],idempotency_key="one",expected_revision=1)
    state.release.set()
    await asyncio.gather(*list(state.scheduler._inflight),return_exceptions=True)
    occurrence=await state.scheduler._occurrences.get(ack["occurrence_id"])
    assert occurrence["status"]=="running"
    run=await state.dao.get_run(occurrence["run_id"])
    assert run["status"]=="completed" and (await state.dao.result_payload(run["id"]))["text"]=="done"
    monkeypatch.setattr(state.scheduler._occurrences,"settle",original)
    await state.scheduler._reconcile_occurrences()
    assert (await state.scheduler._occurrences.get(occurrence["id"]))["status"]=="completed"


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


# ---------------------------------------------------------------------------
# M5-C — scheduled prompts through the RunService
# ---------------------------------------------------------------------------


def test_scheduler_timezone_defaults_to_shanghai(monkeypatch) -> None:
    import zoneinfo

    from minimax_code.scheduler import _scheduler_timezone

    monkeypatch.delenv("MINIMAX_CODE_SCHEDULER_TIMEZONE", raising=False)
    tz = _scheduler_timezone()
    assert tz == "Asia/Shanghai"
    # The zone must be real — a typo would break every CronTrigger.
    assert zoneinfo.ZoneInfo(tz) is not None
    monkeypatch.setenv("MINIMAX_CODE_SCHEDULER_TIMEZONE", "UTC")
    assert _scheduler_timezone() == "UTC"


async def test_prompt_fire_runs_through_runservice(monkeypatch, tmp_path) -> None:
    """A scheduled prompt fires the FULL native loop via send_message —
    tools, permissions, budget and durable run rows — not the old
    single-shot LLM call. Verified by intercepting the entry point and
    asserting the receipt flows into the task bookkeeping."""

    import minimax_code.ipc.builtins as builtins_mod
    import minimax_code.scheduler as sched_mod

    captured: dict[str, Any] = {}

    class _FakeServer:
        pass

    async def fake_send(params: dict, ctx, *, wait: bool = True, **kwargs):
        captured["params"] = params
        captured["wait"] = wait
        await ctx.reply({"session_id": params["session_id"],
                         "message_id": "m1", "run_id": "run_1",
                         "text": "scheduled work done", "run_status": "completed", "stub": True})
        return None

    monkeypatch.setattr(builtins_mod, "handle_agent_send_message", fake_send)
    monkeypatch.setattr(sched_mod, "_IPC_SERVER", _FakeServer(), raising=False)

    from minimax_code.scheduler import JobScheduler

    scheduler = JobScheduler(None)  # The stubbed send path does not access the database.
    result = await scheduler._run_prompt_via_runservice(
        {"prompt": "refresh the dashboard", "job_id": "job-9",
         "budget_tokens": 5000},
        prompt="refresh the dashboard", session_id="ses-sched", occurrence={"id":"offline-occ","context":{"mode":"execute","budget_tokens":5000},"job_id":"job-9","revision":1})

    assert result["ok"] is True
    assert result["output"] == "scheduled work done"
    assert result["run_id"] == "run_1"
    assert result["evidence"] == {"kind": "run", "run_id": "run_1"}
    # The full native-loop contract: explicit session + budget + per-fire
    # idempotency key (minute-window replay guard).
    assert captured["params"]["session_id"] == "ses-sched"
    assert captured["params"]["budget_tokens"] == 5000
    assert captured["params"]["idempotency_key"] == "schedule:offline-occ"
    assert captured["wait"] is False  # accept first so the task can link before waiting

    # A failing turn lands as a structured error for the tasks row.
    async def failing_send(params, ctx, *, wait=True, **kwargs):
        await ctx.reply_error(-32603, "model exploded")
        return None

    monkeypatch.setattr(builtins_mod, "handle_agent_send_message", failing_send)
    result = await scheduler._run_prompt_via_runservice(
        {"prompt": "oops", "job_id": "job-9"},
        prompt="oops", session_id="ses-sched", occurrence={"id":"offline-occ","context":{"mode":"execute","budget_tokens":5000},"job_id":"job-9","revision":1})
    assert result["ok"] is False
    assert "model exploded" in result["error"]


def test_prompt_fire_without_ipc_server_reports_error(monkeypatch) -> None:
    """No IPC server bound (unit context) -> a structured error, not a
    crash — the fire degrades to a failed task row."""
    import asyncio

    import minimax_code.scheduler as sched_mod

    monkeypatch.setattr(sched_mod, "_IPC_SERVER", None, raising=False)
    from minimax_code.scheduler import JobScheduler

    scheduler = JobScheduler.__new__(JobScheduler)
    result = asyncio.run(scheduler._run_prompt_via_runservice(
        {"prompt": "x", "job_id": "j"}, prompt="x", session_id="s"))
    assert result["ok"] is False
    assert "no IPC server bound" in result["error"]
