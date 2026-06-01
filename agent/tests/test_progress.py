"""Tests for the progress-tracking layer.

Covers the three pieces added by the progress-tracking task:

* :class:`TaskDAO` — high-level CRUD (auto-id create, get,
  filtered list, update_progress, complete).
* :class:`ProgressTracker` — DAO wrapper that emits
  ``agent.status`` events through a caller-supplied emit fn.
* ``task.*`` IPC handlers — round-trip through
  :class:`~minimax_code.ipc.client.IPCClient`.

The tests are organized as one scenario per behaviour, sharing
fixtures only at the boundary (a fresh migrated DB per test
function).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from minimax_code.progress import ProgressTracker
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.dao.tasks import TaskDAO
from minimax_code.storage.db import (
    AsyncDatabase,
    Database,
    make_temp_database_path,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh temp DB path per test."""
    return make_temp_database_path(tmp_path)


@pytest.fixture
async def async_db(db_path: Path) -> AsyncDatabase:
    """A migrated async Database."""
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def id_factory() -> Any:
    """Generate prefixed unique ids so test output is greppable."""

    def _make(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:10]}"

    return _make


@pytest.fixture
async def seed_session(async_db: AsyncDatabase, id_factory: Any) -> str:
    """Create a sessions row so FKs to tasks succeed."""
    sdao = SessionsDAO(async_db)
    sid = id_factory("ses")
    await sdao.create(id=sid, title="progress-test")
    return sid


# ---------------------------------------------------------------------------
# TaskDAO CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_dao_create_returns_id(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    """``create(session_id, title)`` returns a generated task id and the
    row is queryable via :meth:`get`."""
    dao = TaskDAO(async_db)
    tid = await dao.create(session_id=seed_session, title="build")
    assert tid.startswith("task_")

    row = await dao.get(tid)
    assert row is not None
    assert row["id"] == tid
    assert row["session_id"] == seed_session
    assert row["title"] == "build"
    # Freshly-created task is pending with 0% progress.
    assert row["status"] == "pending"
    assert row["progress"] == 0
    assert row["created_at"] is not None
    assert row["started_at"] is None
    assert row["completed_at"] is None


@pytest.mark.asyncio
async def test_task_dao_create_validates_inputs(async_db: AsyncDatabase) -> None:
    """Empty / non-string session_id or title raises ``ValueError``."""
    dao = TaskDAO(async_db)
    with pytest.raises(ValueError, match="session_id"):
        await dao.create(session_id="", title="x")
    with pytest.raises(ValueError, match="title"):
        await dao.create(session_id="ses_x", title="")


@pytest.mark.asyncio
async def test_task_dao_get_missing_returns_none(async_db: AsyncDatabase) -> None:
    dao = TaskDAO(async_db)
    assert await dao.get("task_does_not_exist") is None


@pytest.mark.asyncio
async def test_task_dao_update_progress_writes_progress_and_status(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    """A bare ``update_progress(tid, n)`` leaves status alone and writes
    the new percentage."""
    dao = TaskDAO(async_db)
    tid = await dao.create(session_id=seed_session, title="x")
    # Transition into running first so started_at auto-fills.
    await dao.update_progress(tid, 10, status="running")
    after = await dao.update_progress(tid, 50)
    assert after is not None
    assert after["progress"] == 50
    assert after["status"] == "running"
    assert after["started_at"] is not None  # filled by first status=running


@pytest.mark.asyncio
async def test_task_dao_update_progress_validates_range(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    dao = TaskDAO(async_db)
    tid = await dao.create(session_id=seed_session, title="x")
    with pytest.raises(ValueError, match="progress"):
        await dao.update_progress(tid, -1)
    with pytest.raises(ValueError, match="progress"):
        await dao.update_progress(tid, 101)


@pytest.mark.asyncio
async def test_task_dao_complete_marks_completed_at(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    dao = TaskDAO(async_db)
    tid = await dao.create(session_id=seed_session, title="x")
    done = await dao.complete(tid)
    assert done is not None
    assert done["status"] == "completed"
    assert done["progress"] == 100
    assert done["completed_at"] is not None
    assert done["error"] is None


@pytest.mark.asyncio
async def test_task_dao_complete_failed_records_error(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    dao = TaskDAO(async_db)
    tid = await dao.create(session_id=seed_session, title="x")
    done = await dao.complete(tid, status="failed", error="boom")
    assert done is not None
    assert done["status"] == "failed"
    assert done["error"] == "boom"
    assert done["completed_at"] is not None


@pytest.mark.asyncio
async def test_task_dao_complete_rejects_running(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    """``complete(..., status='running')`` is a programming error."""
    dao = TaskDAO(async_db)
    tid = await dao.create(session_id=seed_session, title="x")
    with pytest.raises(ValueError, match="complete"):
        await dao.complete(tid, status="running")


@pytest.mark.asyncio
async def test_task_dao_list_filters_by_session_and_status(
    async_db: AsyncDatabase, id_factory: Any
) -> None:
    """``list(session_id=, status=)`` must AND the filters and order
    newest first."""
    sdao = SessionsDAO(async_db)
    dao = TaskDAO(async_db)
    sid_a = id_factory("ses")
    sid_b = id_factory("ses")
    await sdao.create(id=sid_a, title="a")
    await sdao.create(id=sid_b, title="b")

    # 3 tasks in sid_a, 2 in sid_b; mark some completed.
    a_ids = [await dao.create(session_id=sid_a, title=f"a{i}") for i in range(3)]
    b_ids = [await dao.create(session_id=sid_b, title=f"b{i}") for i in range(2)]
    for tid in a_ids[:2]:
        await dao.complete(tid)

    all_a = await dao.list(session_id=sid_a)
    assert len(all_a) == 3
    assert all(t["session_id"] == sid_a for t in all_a)

    completed_a = await dao.list(session_id=sid_a, status="completed")
    assert len(completed_a) == 2
    assert {t["id"] for t in completed_a} == set(a_ids[:2])

    pending_b = await dao.list(session_id=sid_b, status="pending")
    assert len(pending_b) == 2
    assert {t["id"] for t in pending_b} == set(b_ids)

    # No filter → all 5 across both sessions.
    everything = await dao.list()
    assert len(everything) == 5

    # Invalid status is rejected (not silently treated as no-match).
    with pytest.raises(ValueError, match="status"):
        await dao.list(status="bogus")


# ---------------------------------------------------------------------------
# ProgressTracker — emit callback
# ---------------------------------------------------------------------------


class _EventCollector:
    """In-process replacement for ``ctx`` that records emit calls."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    async def emit(self, event: str, data: dict[str, Any] | None = None) -> None:
        self.events.append((event, data or {}))


@pytest.mark.asyncio
async def test_progress_tracker_start_emits_started(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    """``start_task`` writes a row in 'running' and emits agent.status{status='started'}."""
    tracker = ProgressTracker(TaskDAO(async_db))
    sink = _EventCollector()
    tid = await tracker.start_task(seed_session, "build", emit=sink)

    assert sink.events, "expected at least one emit"
    event_name, payload = sink.events[0]
    assert event_name == "agent.status"
    assert payload["task_id"] == tid
    assert payload["status"] == "started"
    assert payload["detail"]["title"] == "build"
    assert payload["detail"]["session_id"] == seed_session
    # The row is also updated to running.
    row = await tracker.get(tid)
    assert row is not None
    assert row["status"] == "running"
    assert row["started_at"] is not None


@pytest.mark.asyncio
async def test_progress_tracker_update_emits_progress(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    """``update`` writes the new progress and emits ``status='progress'``."""
    tracker = ProgressTracker(TaskDAO(async_db))
    sink = _EventCollector()
    tid = await tracker.start_task(seed_session, "x", emit=sink)
    sink.events.clear()

    await tracker.update(tid, 42, message="halfway", emit=sink)

    assert sink.events
    event_name, payload = sink.events[0]
    assert event_name == "agent.status"
    assert payload["status"] == "progress"
    assert payload["progress"] == 42
    assert payload["task_id"] == tid
    assert payload["detail"]["message"] == "halfway"

    # Row reflects 42%.
    row = await tracker.get(tid)
    assert row["progress"] == 42


@pytest.mark.asyncio
async def test_progress_tracker_complete_success_emits_completed(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    tracker = ProgressTracker(TaskDAO(async_db))
    sink = _EventCollector()
    tid = await tracker.start_task(seed_session, "x", emit=sink)
    sink.events.clear()

    await tracker.complete(tid, emit=sink)

    event_name, payload = sink.events[0]
    assert payload["status"] == "completed"
    assert payload["progress"] == 100
    row = await tracker.get(tid)
    assert row["status"] == "completed"


@pytest.mark.asyncio
async def test_progress_tracker_complete_failure_emits_failed(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    tracker = ProgressTracker(TaskDAO(async_db))
    sink = _EventCollector()
    tid = await tracker.start_task(seed_session, "x", emit=sink)
    sink.events.clear()

    await tracker.complete(tid, success=False, error="nope", emit=sink)

    event_name, payload = sink.events[0]
    assert payload["status"] == "failed"
    assert payload["detail"]["error"] == "nope"
    row = await tracker.get(tid)
    assert row["status"] == "failed"
    assert row["error"] == "nope"


@pytest.mark.asyncio
async def test_progress_tracker_no_emit_when_emit_omitted(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    """Omitting ``emit=`` must not crash and must not write to a sink."""
    tracker = ProgressTracker(TaskDAO(async_db))
    tid = await tracker.start_task(seed_session, "x")
    await tracker.update(tid, 30)
    await tracker.complete(tid)
    row = await tracker.get(tid)
    assert row["status"] == "completed"
    assert row["progress"] == 100


@pytest.mark.asyncio
async def test_progress_tracker_cancel_emits_cancelled(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    tracker = ProgressTracker(TaskDAO(async_db))
    sink = _EventCollector()
    tid = await tracker.start_task(seed_session, "x", emit=sink)
    sink.events.clear()

    row = await tracker.cancel(tid, emit=sink)
    assert row["status"] == "cancelled"
    assert sink.events[0][1]["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Concurrency — 100 racing update_progress calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_progress_tracker_concurrent_updates_do_not_lose(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    """100 concurrent ``update(tid, p)`` calls must all succeed; the
    final stored progress is one of the values that was written
    (no row is silently dropped, no exception leaks out).

    SQLite serialises writes anyway (WAL + single connection),
    so this is a sanity check that the tracker is re-entrant
    under ``asyncio.gather`` and that no update is silently
    swallowed.
    """
    tracker = ProgressTracker(TaskDAO(async_db))
    tid = await tracker.start_task(seed_session, "burst")

    async def bump(p: int) -> None:
        await tracker.update(tid, p)

    # ``return_exceptions=True`` so we can assert on any failure
    # explicitly instead of letting gather raise.
    results = await asyncio.gather(
        *(bump(p) for p in range(100)), return_exceptions=True
    )
    errors = [r for r in results if isinstance(r, BaseException)]
    assert not errors, f"concurrent update_progress raised: {errors!r}"

    # 100 distinct values were applied (0..99). The final stored
    # value is one of them — but *some* value is stored; the row
    # is not NULL.
    final = await tracker.get(tid)
    assert final is not None
    assert 0 <= final["progress"] <= 99

    # And the number of distinct values that landed in the row
    # is, by construction, just the last writer's value. We can
    # at least check the row is consistent (status still running,
    # progress an integer).
    assert final["status"] == "running"
    assert isinstance(final["progress"], int)


@pytest.mark.asyncio
async def test_progress_tracker_concurrent_with_emit_collects_all(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    """Concurrent updates with a shared emit sink: every emit must
    land in the collector with a unique progress value."""
    tracker = ProgressTracker(TaskDAO(async_db))
    sink = _EventCollector()
    tid = await tracker.start_task(seed_session, "x", emit=sink)
    sink.events.clear()

    async def bump(p: int) -> None:
        await tracker.update(tid, p, emit=sink)

    await asyncio.gather(*(bump(p) for p in range(50)))

    # Filter to the progress events (skip the started event).
    progress_events = [e for e in sink.events if e[1].get("status") == "progress"]
    assert len(progress_events) == 50
    # The 0..49 set is complete — no progress value was lost.
    assert {e[1]["progress"] for e in progress_events} == set(range(50))


# ---------------------------------------------------------------------------
# IPC handler round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_task_ipc_list_and_get_round_trip(
    async_db: AsyncDatabase, seed_session: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive ``task.list`` and ``task.get`` through the in-process
    ``IPCClient`` to make sure they're registered and serialise
    rows correctly."""
    from minimax_code.app import set_progress_tracker
    from minimax_code.ipc.client import IPCClient
    from minimax_code.storage.dao.tasks import TaskDAO as _TaskDAO
    from minimax_code.progress import ProgressTracker as _Tracker

    # Inject a tracker that points at our test DB so the handlers
    # see a populated task list.
    tracker = _Tracker(_TaskDAO(async_db))
    set_progress_tracker(tracker)
    try:
        tid = await tracker.start_task(seed_session, "ipc-test")
        client = IPCClient()
        listed = await client.request("task.list", {"session_id": seed_session})
        assert any(t["id"] == tid for t in listed["tasks"])
        got = await client.request("task.get", {"task_id": tid})
        assert got["task"]["id"] == tid
        assert got["task"]["title"] == "ipc-test"
    finally:
        set_progress_tracker(None)


@pytest.mark.asyncio
async def test_task_ipc_cancel_round_trip(
    async_db: AsyncDatabase, seed_session: str
) -> None:
    from minimax_code.app import set_progress_tracker
    from minimax_code.ipc.client import IPCClient
    from minimax_code.storage.dao.tasks import TaskDAO as _TaskDAO
    from minimax_code.progress import ProgressTracker as _Tracker

    tracker = _Tracker(_TaskDAO(async_db))
    set_progress_tracker(tracker)
    try:
        tid = await tracker.start_task(seed_session, "to-cancel")
        client = IPCClient()
        reply = await client.request("task.cancel", {"task_id": tid})
        assert reply["ok"] is True
        assert reply["task"]["status"] == "cancelled"
    finally:
        set_progress_tracker(None)


@pytest.mark.asyncio
async def test_task_ipc_unknown_task_id_returns_error(
    async_db: AsyncDatabase,
) -> None:
    from minimax_code.app import set_progress_tracker
    from minimax_code.ipc.client import IPCClient
    from minimax_code.storage.dao.tasks import TaskDAO as _TaskDAO
    from minimax_code.progress import ProgressTracker as _Tracker

    set_progress_tracker(_Tracker(_TaskDAO(async_db)))
    try:
        client = IPCClient()
        with pytest.raises(Exception) as excinfo:
            await client.request("task.get", {"task_id": "task_nope"})
        # The client raises RuntimeError with the error payload.
        assert "task_id" in str(excinfo.value)
    finally:
        set_progress_tracker(None)


@pytest.mark.asyncio
async def test_task_ipc_no_tracker_returns_internal_error() -> None:
    """If the storage layer didn't open, the handler returns a clear
    INTERNAL_ERROR rather than a stack trace."""
    from minimax_code.app import set_progress_tracker
    from minimax_code.ipc.client import IPCClient

    set_progress_tracker(None)
    client = IPCClient()
    with pytest.raises(Exception) as excinfo:
        await client.request("task.list", {})
    assert "storage" in str(excinfo.value).lower() or "unavailable" in str(
        excinfo.value
    ).lower()
