"""Tests for R12 — crash detection + orphan-run recovery.

Two independent layers, fused from grok-build's ``xai-crash-handler`` +
``cleanup_stale_sessions``:

1. The marker-file protocol in :mod:`minimax_code.runtime.crash_detect`.
2. The append-only orphan recovery on :class:`AgentRunsDAO`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minimax_code.runtime.crash_detect import (
    check_previous_crash,
    mark_clean_exit,
    mark_dirty_start,
)
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase


@pytest.fixture
async def db(tmp_path: Path):
    """A migrated AsyncDatabase isolated under ``tmp_path``."""
    database = AsyncDatabase(tmp_path / "test.db")
    await database.connect()
    await database.migrate()
    yield database
    await database.close()


async def _seed_session(db: AsyncDatabase, sid: str) -> None:
    """Insert a parent session row so ``agent_runs.session_id`` FK holds."""
    await SessionsDAO(db).create(id=sid)


# ---------------------------------------------------------------------------
# Marker-file protocol
# ---------------------------------------------------------------------------


class TestCrashMarker:
    def test_clean_start_returns_none(self, tmp_path: Path):
        assert check_previous_crash(tmp_path) is None

    def test_dirty_marker_is_detected(self, tmp_path: Path):
        mark_dirty_start(tmp_path)
        report = check_previous_crash(tmp_path)
        assert report is not None
        assert report.crashed_pid > 0  # the live process's pid
        assert report.started_at  # ISO timestamp recorded
        assert report.detected_at

    def test_marker_consumed_after_detection(self, tmp_path: Path):
        mark_dirty_start(tmp_path)
        assert check_previous_crash(tmp_path) is not None
        # A single crash is reported exactly once — the marker is gone.
        assert check_previous_crash(tmp_path) is None

    def test_clean_exit_clears_marker(self, tmp_path: Path):
        mark_dirty_start(tmp_path)
        mark_clean_exit(tmp_path)
        assert check_previous_crash(tmp_path) is None


# ---------------------------------------------------------------------------
# Orphan-run recovery (DAO layer)
# ---------------------------------------------------------------------------


class TestRecoverOrphans:
    async def test_idempotent_on_clean_db(self, db: AsyncDatabase):
        dao = AgentRunsDAO(db)
        result = await dao.recover_orphans()
        assert result["recovered"] == 0
        assert result["run_ids"] == []
        assert result["sessions"] == []

    async def test_flips_running_to_failed_and_appends_step(self, db: AsyncDatabase):
        await _seed_session(db, "sess_a")
        dao = AgentRunsDAO(db)
        run = await dao.create_run(session_id="sess_a", mode="chat", status="running")
        # Sanity: the run is genuinely in flight before recovery.
        assert (await dao.get_run(run["id"]))["status"] == "running"

        result = await dao.recover_orphans()
        assert result["recovered"] == 1
        assert run["id"] in result["run_ids"]

        after = await dao.get_run(run["id"])
        assert after["status"] == "failed"
        assert after["error"]  # recovery reason recorded

        # Append-only audit: a recovery status step is visible on the
        # timeline rather than a silent status flip.
        steps = await dao.list_steps(run["id"])
        assert any("recovered" in (s.get("title") or "") for s in steps)

    async def test_only_in_flight_runs_recovered(self, db: AsyncDatabase):
        for sid in ("s1", "s2", "s3", "s4"):
            await _seed_session(db, sid)
        dao = AgentRunsDAO(db)
        await dao.create_run(session_id="s1", status="running")
        await dao.create_run(session_id="s2", status="completed")
        await dao.create_run(session_id="s3", status="awaiting_approval")
        await dao.create_run(session_id="s4", status="cancelled")
        result = await dao.recover_orphans()
        # running + awaiting_approval are in flight; completed/cancelled stay.
        assert result["recovered"] == 2

    async def test_double_recovery_is_idempotent(self, db: AsyncDatabase):
        await _seed_session(db, "s1")
        dao = AgentRunsDAO(db)
        await dao.create_run(session_id="s1", status="running")
        first = await dao.recover_orphans()
        assert first["recovered"] == 1
        # After the first pass the run is already ``failed`` → not in flight.
        second = await dao.recover_orphans()
        assert second["recovered"] == 0
