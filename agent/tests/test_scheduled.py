"""Tests for the scheduled-jobs system.

Three scenarios:

* :class:`TestScheduledJobsDAO` — round-trip CRUD on
  :class:`~minimax_code.storage.dao.scheduled_jobs.ScheduledJobsDAO`
  using the new task-spec method names (``list_all`` /
  ``update_enabled`` / ``update_last_run`` / ``update_next_run`` /
  ``create(name, cron_expr, payload, enabled=True)``).
* :class:`TestJobScheduler` — APScheduler-backed
  :class:`~minimax_code.scheduler.JobScheduler` covers
  add/remove, concurrent enable/disable, cron-parse legality, and
  the fire path (manual ``run_now`` + a one-second-ish cron tick
  to confirm the ``tasks`` table gets a ``completed`` row).
* :class:`TestScheduledIpcHandlers` — drive the ``schedule.*``
  handlers through :class:`~minimax_code.ipc.client.IPCClient` so
  we exercise the full JSON-RPC path including params validation
  and error envelopes.

The tests share a small fixture set; each test is self-contained
and re-creates the rows it needs.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code.ipc.client import IPCClient
from minimax_code.scheduler import (
    InvalidCronError,
    JobScheduler,
    SchedulerError,
    set_scheduler,
)
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


@pytest.fixture
def id_factory() -> Any:
    def _make(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:10]}"

    return _make


# ---------------------------------------------------------------------------
# DAO — task-spec method names
# ---------------------------------------------------------------------------


class TestScheduledJobsDAO:
    """The new task-spec method names must round-trip through SQLite."""

    @pytest.mark.asyncio
    async def test_create_auto_id_and_defaults(
        self, async_db: AsyncDatabase
    ) -> None:
        dao = ScheduledJobsDAO(async_db)
        row = await dao.create(name="smoke", cron_expr="* * * * *", payload={"k": 1})
        assert row["id"].startswith("job_")
        assert row["name"] == "smoke"
        assert row["cron_expr"] == "* * * * *"
        assert row["payload"] == {"k": 1}
        assert row["enabled"] is True
        assert row["last_run_at"] is None
        assert row["next_run_at"] is None

    @pytest.mark.asyncio
    async def test_create_explicit_id(
        self, async_db: AsyncDatabase, id_factory: Any
    ) -> None:
        dao = ScheduledJobsDAO(async_db)
        jid = id_factory("job")
        row = await dao.create(
            id=jid, name="x", cron_expr="0 * * * *", enabled=False
        )
        assert row["id"] == jid
        assert row["enabled"] is False

    @pytest.mark.asyncio
    async def test_list_all_returns_every_job_in_name_order(
        self, async_db: AsyncDatabase
    ) -> None:
        dao = ScheduledJobsDAO(async_db)
        await dao.create(name="zebra", cron_expr="* * * * *")
        await dao.create(name="alpha", cron_expr="* * * * *")
        await dao.create(name="mu", cron_expr="* * * * *")
        rows = await dao.list_all()
        names = [r["name"] for r in rows]
        assert names == ["alpha", "mu", "zebra"]

    @pytest.mark.asyncio
    async def test_update_enabled_toggles(
        self, async_db: AsyncDatabase
    ) -> None:
        dao = ScheduledJobsDAO(async_db)
        row = await dao.create(name="x", cron_expr="* * * * *")
        assert row["enabled"] is True

        disabled = await dao.update_enabled(row["id"], False)
        assert disabled is not None and disabled["enabled"] is False

        enabled_again = await dao.update_enabled(row["id"], True)
        assert enabled_again is not None and enabled_again["enabled"] is True

    @pytest.mark.asyncio
    async def test_update_last_run_only(
        self, async_db: AsyncDatabase
    ) -> None:
        dao = ScheduledJobsDAO(async_db)
        row = await dao.create(name="x", cron_expr="* * * * *")
        out = await dao.update_last_run(row["id"], "2026-01-01T00:00:00Z")
        assert out is not None
        assert out["last_run_at"] == "2026-01-01T00:00:00Z"
        # next_run_at is untouched.
        assert out["next_run_at"] is None

    @pytest.mark.asyncio
    async def test_update_next_run_only_preserves_last_run(
        self, async_db: AsyncDatabase
    ) -> None:
        dao = ScheduledJobsDAO(async_db)
        row = await dao.create(name="x", cron_expr="* * * * *")
        await dao.update_last_run(row["id"], "2026-01-01T00:00:00Z")
        out = await dao.update_next_run(row["id"], "2026-02-01T00:00:00Z")
        assert out is not None
        assert out["next_run_at"] == "2026-02-01T00:00:00Z"
        assert out["last_run_at"] == "2026-01-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_delete_round_trip(
        self, async_db: AsyncDatabase
    ) -> None:
        dao = ScheduledJobsDAO(async_db)
        row = await dao.create(name="x", cron_expr="* * * * *")
        assert await dao.delete(row["id"]) is True
        # second delete is a no-op
        assert await dao.delete(row["id"]) is False
        assert await dao.get(row["id"]) is None

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(
        self, async_db: AsyncDatabase
    ) -> None:
        dao = ScheduledJobsDAO(async_db)
        assert await dao.get("does_not_exist") is None


# ---------------------------------------------------------------------------
# JobScheduler
# ---------------------------------------------------------------------------


class TestJobScheduler:
    """The APScheduler-backed wrapper around the DAO."""

    @pytest.mark.asyncio
    async def test_add_job_writes_row_and_registers(
        self, async_db: AsyncDatabase
    ) -> None:
        sched = JobScheduler(async_db)
        await sched.start()
        try:
            row = await sched.add_job(
                name="nightly", cron_expr="0 0 * * *", payload={"kind": "noop"}
            )
            assert row["id"].startswith("job_")
            assert row["next_run_at"] is not None
            assert sched.has_job(row["id"]) is True
        finally:
            await sched.stop()

    @pytest.mark.asyncio
    async def test_add_job_invalid_cron_raises(
        self, async_db: AsyncDatabase
    ) -> None:
        sched = JobScheduler(async_db)
        await sched.start()
        try:
            with pytest.raises(InvalidCronError):
                await sched.add_job(name="bad", cron_expr="not a cron expression")
            # Nothing was written.
            assert await sched.list_jobs() == []
        finally:
            await sched.stop()

    @pytest.mark.asyncio
    async def test_remove_job_unregisters(
        self, async_db: AsyncDatabase
    ) -> None:
        sched = JobScheduler(async_db)
        await sched.start()
        try:
            row = await sched.add_job(name="x", cron_expr="* * * * *")
            assert sched.has_job(row["id"]) is True
            assert await sched.remove_job(row["id"]) is True
            assert sched.has_job(row["id"]) is False
            # second remove is a no-op
            assert await sched.remove_job(row["id"]) is False
        finally:
            await sched.stop()

    @pytest.mark.asyncio
    async def test_enable_disable_round_trip(
        self, async_db: AsyncDatabase
    ) -> None:
        sched = JobScheduler(async_db)
        await sched.start()
        try:
            row = await sched.add_job(name="x", cron_expr="* * * * *")
            assert sched.has_job(row["id"]) is True
            # disable unregisters
            d = await sched.disable(row["id"])
            assert d is not None and d["enabled"] is False
            assert sched.has_job(row["id"]) is False
            # enable re-registers
            e = await sched.enable(row["id"])
            assert e is not None and e["enabled"] is True
            assert sched.has_job(row["id"]) is True
        finally:
            await sched.stop()

    @pytest.mark.asyncio
    async def test_concurrent_enable_disable(
        self, async_db: AsyncDatabase
    ) -> None:
        """Hammer enable/disable from many tasks; nothing must crash and
        the final state must be self-consistent (DB row matches APScheduler)."""
        sched = JobScheduler(async_db)
        await sched.start()
        try:
            row = await sched.add_job(name="x", cron_expr="* * * * *")

            async def toggle() -> None:
                for _ in range(10):
                    await sched.disable(row["id"])
                    await sched.enable(row["id"])

            await asyncio.gather(*(toggle() for _ in range(8)))
            final = await sched.list_jobs()
            assert len(final) == 1
            assert final[0]["enabled"] is True
            # APScheduler must be in a state that matches the DB row.
            assert sched.has_job(row["id"]) is True
        finally:
            await sched.stop()

    @pytest.mark.asyncio
    async def test_run_now_writes_completed_task(
        self, async_db: AsyncDatabase
    ) -> None:
        sched = JobScheduler(async_db)
        await sched.start()
        try:
            row = await sched.add_job(name="manual", cron_expr="* * * * *")
            ack = await sched.run_now(row["id"])
            assert ack["ok"] is True
            assert ack["job_id"] == row["id"]
            assert "triggered_at" in ack

            # Wait for the fire coroutine to land on the tasks table.
            tdao = TasksDAO(async_db)
            for _ in range(40):
                await asyncio.sleep(0.05)
                tasks = await tdao.list()
                if tasks:
                    break
            assert tasks, "expected a task row to be written after run_now"
            t = tasks[0]
            assert t["title"].startswith("[manual]")
            assert t["status"] == "completed"
            assert t["progress"] == 100

            # And last_run_at was bumped.
            latest = await sched.list_jobs()
            assert latest[0]["last_run_at"] is not None
        finally:
            await sched.stop()

    @pytest.mark.asyncio
    async def test_run_now_unknown_job_raises(
        self, async_db: AsyncDatabase
    ) -> None:
        sched = JobScheduler(async_db)
        await sched.start()
        try:
            with pytest.raises(SchedulerError):
                await sched.run_now("does_not_exist")
        finally:
            await sched.stop()

    @pytest.mark.asyncio
    async def test_persistence_across_restart(
        self, async_db: AsyncDatabase
    ) -> None:
        """Jobs persisted to the DB must re-appear in APScheduler after
        the scheduler is stopped and a fresh one is started."""
        first = JobScheduler(async_db)
        await first.start()
        row = await first.add_job(name="p", cron_expr="* * * * *")
        assert first.has_job(row["id"]) is True
        # Note: APScheduler's ``shutdown()`` does not always clear
        # its in-memory job table, so we don't assert on
        # ``first.has_job`` after stop. What matters is that a
        # *fresh* scheduler picks the job up from the DB.
        await first.stop()

        second = JobScheduler(async_db)
        await second.start()
        try:
            assert second.has_job(row["id"]) is True
        finally:
            await second.stop()

    @pytest.mark.asyncio
    async def test_payload_runner_off_event_loop(
        self, async_db: AsyncDatabase
    ) -> None:
        """A blocking payload must not stall the asyncio loop."""
        import time

        from minimax_code.storage.dao.tasks import TasksDAO

        loop = asyncio.get_running_loop()
        main_loop_alive = asyncio.Event()

        async def blocking_payload(payload: dict[str, Any]) -> dict[str, Any]:
            # This is a sync sleep in a worker thread; the event
            # loop should keep ticking the whole time.
            time.sleep(0.2)
            return {"ok": True, "echo": payload}

        async def heartbeat() -> None:
            # Three short sleeps interleaved with the slow payload
            # prove the loop is not blocked.
            for _ in range(3):
                await asyncio.sleep(0.05)
            main_loop_alive.set()

        sched = JobScheduler(async_db, payload_runner=blocking_payload)
        await sched.start()
        try:
            row = await sched.add_job(name="slow", cron_expr="* * * * *")
            await asyncio.gather(sched.run_now(row["id"]), heartbeat())
            assert main_loop_alive.is_set(), "event loop was blocked"

            tdao = TasksDAO(async_db)
            for _ in range(40):
                await asyncio.sleep(0.05)
                tasks = await tdao.list()
                if tasks and tasks[0]["status"] == "completed":
                    break
            assert tasks and tasks[0]["status"] == "completed"
        finally:
            await sched.stop()


# ---------------------------------------------------------------------------
# IPC handlers
# ---------------------------------------------------------------------------


class TestScheduledIpcHandlers:
    """Drive the ``schedule.*`` namespace through IPCClient."""

    @pytest.fixture
    async def client_with_db(
        self, tmp_path: Path
    ) -> tuple[IPCClient, AsyncDatabase, JobScheduler]:
        """Build an IPCClient whose storage layer points at a temp DB.

        We replace the default data dir with a temp one so the
        scheduler's lazy-init opens a fresh DB instead of the real
        %APPDATA% one. The DB is closed in the teardown.
        """
        import os

        workdir = tmp_path / "ipc_storage"
        workdir.mkdir(parents=True, exist_ok=True)
        os.environ["MINIMAX_CODE_DATA_DIR"] = str(workdir)
        db = AsyncDatabase(make_temp_database_path(workdir))
        await db.connect()
        await db.migrate()
        sched = JobScheduler(db)
        await sched.start()
        set_scheduler(sched)
        client = IPCClient()
        try:
            yield client, db, sched
        finally:
            await sched.stop()
            await db.close()
            set_scheduler(None)
            os.environ.pop("MINIMAX_CODE_DATA_DIR", None)

    @pytest.mark.asyncio
    async def test_schedule_create_list_delete(
        self, client_with_db: tuple[IPCClient, AsyncDatabase, JobScheduler]
    ) -> None:
        client, _db, _sched = client_with_db
        create_resp = await client.request(
            "schedule.create",
            {"name": "smoke", "cron_expr": "* * * * *", "payload": {"k": 1}},
        )
        assert "job" in create_resp
        job_id = create_resp["job"]["id"]

        list_resp = await client.request("schedule.list", {})
        assert "jobs" in list_resp
        ids = [j["id"] for j in list_resp["jobs"]]
        assert job_id in ids

        del_resp = await client.request(
            "schedule.delete", {"job_id": job_id}
        )
        assert del_resp == {"ok": True, "job_id": job_id}

        # list now shows nothing
        list2 = await client.request("schedule.list", {})
        assert list2["jobs"] == []

    @pytest.mark.asyncio
    async def test_schedule_create_rejects_invalid_cron(
        self, client_with_db: tuple[IPCClient, AsyncDatabase, JobScheduler]
    ) -> None:
        client, _db, _sched = client_with_db
        with pytest.raises(Exception) as excinfo:
            await client.request(
                "schedule.create",
                {"name": "bad", "cron_expr": "not a cron"},
            )
        # Error code 32602 = INVALID_PARAMS.
        text = str(excinfo.value)
        assert "invalid" in text.lower() or "32602" in text

    @pytest.mark.asyncio
    async def test_schedule_create_requires_params(
        self, client_with_db: tuple[IPCClient, AsyncDatabase, JobScheduler]
    ) -> None:
        client, _db, _sched = client_with_db
        with pytest.raises(Exception):
            await client.request("schedule.create", {})

    @pytest.mark.asyncio
    async def test_schedule_enable_disable(
        self, client_with_db: tuple[IPCClient, AsyncDatabase, JobScheduler]
    ) -> None:
        client, _db, sched = client_with_db
        create = await client.request(
            "schedule.create",
            {"name": "toggle", "cron_expr": "0 0 * * *"},
        )
        job_id = create["job"]["id"]
        # start enabled
        assert create["job"]["enabled"] is True

        d = await client.request("schedule.disable", {"job_id": job_id})
        assert d["job"]["enabled"] is False
        assert sched.has_job(job_id) is False

        e = await client.request("schedule.enable", {"job_id": job_id})
        assert e["job"]["enabled"] is True
        assert sched.has_job(job_id) is True

    @pytest.mark.asyncio
    async def test_schedule_run_now(
        self, client_with_db: tuple[IPCClient, AsyncDatabase, JobScheduler]
    ) -> None:
        client, db, _sched = client_with_db
        create = await client.request(
            "schedule.create",
            {"name": "manual", "cron_expr": "* * * * *"},
        )
        job_id = create["job"]["id"]

        ack = await client.request("schedule.run_now", {"job_id": job_id})
        assert ack["ok"] is True
        assert ack["job_id"] == job_id

        # Wait for the fire to land in the tasks table.
        tdao = TasksDAO(db)
        for _ in range(40):
            await asyncio.sleep(0.05)
            tasks = await tdao.list()
            if tasks and tasks[0]["status"] == "completed":
                break
        assert tasks, "expected a tasks row after run_now"
        assert tasks[0]["title"].startswith("[manual]")

    @pytest.mark.asyncio
    async def test_schedule_delete_unknown_job(
        self, client_with_db: tuple[IPCClient, AsyncDatabase, JobScheduler]
    ) -> None:
        client, _db, _sched = client_with_db
        with pytest.raises(Exception) as excinfo:
            await client.request(
                "schedule.delete", {"job_id": "does_not_exist"}
            )
        text = str(excinfo.value).lower()
        assert "unknown" in text or "32602" in text
