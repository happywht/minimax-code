"""APScheduler-backed cron scheduler with DB persistence.

This module is the runtime side of the scheduled jobs system. It owns
an :class:`apscheduler.schedulers.asyncio.AsyncIOScheduler` instance,
mirrors the ``scheduled_jobs`` table into APScheduler jobs, and
translates cron fires into ``tasks``-table rows so the UI / IPC can
observe them.

Why APScheduler
---------------
We need cron expressions with second/minute precision, persistence
across restarts (we re-load from SQLite on start), and an async event
loop that does not block while the user payload runs. APScheduler's
``AsyncIOScheduler`` with a thread-pool executor is a good fit:
``AsyncIOScheduler`` is asyncio-aware, and we use
``loop.run_in_executor`` to keep user payloads off the event loop.

Wiring
------
* :class:`JobScheduler` wraps an :class:`AsyncIOScheduler` and a
  :class:`~minimax_code.storage.dao.scheduled_jobs.ScheduledJobsDAO`.
* ``start()`` re-registers every ``enabled=True`` row in
  ``scheduled_jobs`` so a fresh process picks up where it left off.
* ``add_job`` / ``remove_job`` / ``enable`` / ``disable`` / ``list_jobs``
  are the public mutators used by the IPC handlers.
* ``run_now(job_id)`` triggers an immediate run of the job's payload
  (used by the ``schedule.run_now`` RPC).
* Cron parsing is delegated to APScheduler's
  :class:`~apscheduler.triggers.cron.CronTrigger` so the spec matches
  what the rest of the agent will see when we surface ``next_run_at``.

The scheduler is created lazily — :func:`get_scheduler` returns the
process-wide singleton, building it on first use so the IPC handlers
can register a reference without a startup-order constraint.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from typing import Any, Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..storage.dao.scheduled_jobs import ScheduledJobsDAO
from ..storage.dao.sessions import SessionsDAO
from ..storage.dao.tasks import TasksDAO
from ..storage.db import AsyncDatabase
from ..storage.dao._base import now_iso

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: System-owned session used as the parent for cron-fired task rows.
#: ``tasks.session_id`` is a non-null FK to ``sessions.id``, so cron
#: tasks need a real session row — we mint a single "scheduled" one
#: the first time the scheduler fires and reuse it thereafter.
_SCHEDULED_SESSION_NAME = "scheduled-tasks"
_SCHEDULED_SESSION_ID_PREFIX = "ses_sched_"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class SchedulerError(Exception):
    """Public sentinel — raised for any user-facing scheduler failure."""


class InvalidCronError(SchedulerError):
    """Raised when a cron expression cannot be parsed by APScheduler."""


# ---------------------------------------------------------------------------
# JobScheduler
# ---------------------------------------------------------------------------


#: A user payload may be an async coroutine factory (preferred) or a
#: sync callable. We wrap both behind a uniform ``Callable[[dict], Awaitable[Any]]``.
PayloadFn = Callable[[dict[str, Any]], Awaitable[Any]]


class JobScheduler:
    """APScheduler-backed wrapper around the ``scheduled_jobs`` table.

    Parameters
    ----------
    db:
        An open + migrated :class:`~minimax_code.storage.db.AsyncDatabase`.
        We never close it — caller owns the lifecycle.
    payload_runner:
        Optional ``async def runner(job, payload) -> str`` invoked for
        every fire. The default runner just returns a no-op string,
        keeping the scheduler testable without an LLM. The IPC layer
        plugs in the real runner when the runtime is ready.
    executor:
        Optional ``concurrent.futures.Executor`` used for sync user
        payloads so they don't block the event loop. Defaults to the
        default thread pool.
    """

    def __init__(
        self,
        db: AsyncDatabase,
        *,
        payload_runner: PayloadFn | None = None,
        executor: Any = None,
    ) -> None:
        self._db = db
        self._dao = ScheduledJobsDAO(db)
        self._tasks = TasksDAO(db)
        self._sessions = SessionsDAO(db)
        self._aps: AsyncIOScheduler = AsyncIOScheduler(
            executors={"default": {"type": "threadpool", "max_workers": 4}}
            if executor is None
            else {"default": executor}
        )
        self._payload_runner: PayloadFn = payload_runner or _noop_runner
        self._start_lock = asyncio.Lock()
        self._started = False
        # We need a stable mapping APScheduler job id -> our job id so
        # we can look up the row when a fire happens. APScheduler uses
        # arbitrary string keys; we set ours to ``job_<row_id>``.
        # Guarded by a lock because ``apscheduler`` itself is mostly
        # single-threaded but our wrapper methods might be called from
        # the IPC layer concurrently.
        self._id_lock = threading.Lock()
        self._scheduled_session_id: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the underlying APScheduler and register persisted jobs."""
        async with self._start_lock:
            if self._started:
                return
            if not self._aps.running:
                self._aps.start()
            self._started = True
            # Re-load every enabled job from the DB. We do this AFTER
            # the scheduler has started so the add_job calls have a
            # running backend.
            await self._reload_from_db()

    async def stop(self, *, wait: bool = True) -> None:
        """Stop the scheduler. Safe to call multiple times."""
        async with self._start_lock:
            if not self._started:
                return
            if self._aps.running:
                self._aps.shutdown(wait=wait)
            self._started = False

    @property
    def started(self) -> bool:
        return self._started

    # ------------------------------------------------------------------
    # Public mutators
    # ------------------------------------------------------------------

    async def add_job(
        self,
        *,
        name: str,
        cron_expr: str,
        payload: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Create a new scheduled job in the DB and register it with APScheduler.

        Returns the persisted dict (with ``id``, ``next_run_at``, …).
        Raises :class:`InvalidCronError` if ``cron_expr`` is malformed.
        """
        # Validate the cron expression eagerly so the caller fails fast.
        next_run = _parse_cron(cron_expr)  # raises InvalidCronError

        row = await self._dao.create(
            name=name,
            cron_expr=cron_expr,
            payload=payload,
            enabled=enabled,
        )
        # Persist the computed next_run_at so a UI list shows it
        # without having to query APScheduler.
        await self._dao.update_next_run(row["id"], next_run)

        if enabled and self._started:
            self._register_with_aps(row)

        return await self._dao.get(row["id"]) or row

    async def remove_job(self, job_id: str) -> bool:
        """Delete a job. Returns ``True`` if a row was removed."""
        # Unregister first so we never fire a row that was just deleted.
        self._unregister_from_aps(job_id)
        return await self._dao.delete(job_id)

    async def enable(self, job_id: str) -> dict[str, Any] | None:
        row = await self._dao.enable(job_id)
        if row is not None and self._started and row["enabled"]:
            # Re-register if it had been disabled (or never registered
            # because the scheduler started before the row existed).
            if self._aps.get_job(_aps_id(job_id)) is None:
                self._register_with_aps(row)
        return row

    async def disable(self, job_id: str) -> dict[str, Any] | None:
        self._unregister_from_aps(job_id)
        return await self._dao.disable(job_id)

    async def list_jobs(self) -> list[dict[str, Any]]:
        """Return every job (enabled or not), as stored in the DB."""
        return await self._dao.list_all()

    async def run_now(self, job_id: str) -> dict[str, Any]:
        """Trigger an immediate run of ``job_id``'s payload.

        The fire runs through the same code path as a real cron tick,
        so the resulting ``tasks`` row looks identical to the user.
        We do NOT update ``last_run_at`` here — that's the cron
        trigger's job. ``run_now`` is an "execute this once" command,
        not a "fire the schedule" command.
        """
        row = await self._dao.get(job_id)
        if row is None:
            raise SchedulerError(f"unknown job_id: {job_id!r}")
        # Schedule the run on the event loop so the IPC reply is
        # delivered promptly even if the payload is slow.
        loop = asyncio.get_running_loop()
        loop.create_task(self._fire(row, source="manual"))
        # We don't have a task id back yet; the row will appear in
        # ``task.list`` after the payload completes. The IPC handler
        # just acknowledges the kick.
        return {
            "ok": True,
            "job_id": job_id,
            "triggered_at": now_iso(),
        }

    # ------------------------------------------------------------------
    # Internal: APScheduler plumbing
    # ------------------------------------------------------------------

    def _register_with_aps(self, row: dict[str, Any]) -> None:
        job_id = row["id"]
        aps_id = _aps_id(job_id)
        # Replace any existing job with the same id.
        if self._aps.get_job(aps_id) is not None:
            self._aps.remove_job(aps_id)

        def _tick() -> None:
            # ``AsyncIOScheduler`` runs jobs on the event loop, but
            # we still want to keep payload execution off-loop. We
            # dispatch through the same coroutine path used by
            # ``run_now`` so the bookkeeping (tasks row, last_run_at)
            # is identical between manual and cron triggers.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop in this thread — fall back to
                # creating a fresh one. Should not happen in normal
                # AsyncIOScheduler usage, but be defensive.
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(self._fire(row, source="cron"))
                finally:
                    loop.close()
                return
            loop.create_task(self._fire(row, source="cron"))

        try:
            self._aps.add_job(
                _tick,
                CronTrigger.from_crontab(row["cron_expr"]),
                id=aps_id,
                replace_existing=True,
                max_instances=1,
                coalesce=True,
                misfire_grace_time=60,
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("failed to register job %s with APScheduler", job_id)
            raise SchedulerError(f"failed to register job {job_id}: {exc}") from exc

    def _unregister_from_aps(self, job_id: str) -> None:
        with self._id_lock:
            aps_id = _aps_id(job_id)
            if self._aps.get_job(aps_id) is not None:
                try:
                    self._aps.remove_job(aps_id)
                except Exception:  # pragma: no cover — defensive
                    logger.debug("aps.remove_job(%s) raised", aps_id, exc_info=True)

    async def _reload_from_db(self) -> None:
        rows = await self._dao.list(enabled=True, order_by="name ASC")
        for row in rows:
            try:
                self._register_with_aps(row)
            except SchedulerError as exc:
                logger.warning("skipping job %s on reload: %s", row["id"], exc)
        logger.info("scheduler reloaded %d enabled job(s)", len(rows))

    async def _fire(self, row: dict[str, Any], *, source: str) -> None:
        """Execute a job's payload, persist a ``tasks`` row, update ``last_run_at``."""
        job_id = row["id"]
        payload = row.get("payload") or {}
        session_id = await self._ensure_scheduled_session()
        task_id = f"task_{uuid.uuid4().hex[:10]}"
        started_at = now_iso()

        # Write a "running" task row first so the UI can show progress.
        try:
            await self._tasks.create(
                id=task_id,
                session_id=session_id,
                title=f"[{source}] {row['name']}",
                status="running",
                progress=10,
                started_at=started_at,
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("failed to insert running task row for %s", job_id)
            return

        # Run the user payload off the event loop so a slow or
        # CPU-bound callback doesn't stall IPC.
        loop = asyncio.get_running_loop()
        try:
            result = await loop.run_in_executor(
                None, _run_payload_sync, self._payload_runner, payload
            )
        except BaseException as exc:  # sync runner raised
            logger.exception("payload for job %s raised", job_id)
            await self._tasks.update_status(
                task_id,
                status="failed",
                progress=100,
                error=f"{type(exc).__name__}: {exc}",
            )
            # Still record the run so the UI can show "last run failed".
            await self._dao.update_last_run(job_id, now_iso())
            return

        # Build the "result" string the task row stores. We accept
        # either a string or any JSON-serialisable structure.
        result_text = _stringify_payload_result(result)

        await self._tasks.update_status(
            task_id,
            status="completed",
            progress=100,
            error=None,
        )
        # If the payload returned a structured dict with "error" or
        # "ok=false" we record the error string in the task row too
        # so the UI can surface the failure reason.
        if isinstance(result, dict) and result.get("error"):
            await self._tasks.update_status(
                task_id, status="failed", progress=100, error=str(result["error"])
            )

        # Compute the next run *now* so the UI list is current even if
        # APScheduler is slow to publish next_fire_time.
        try:
            cron = CronTrigger.from_crontab(row["cron_expr"])
            nxt = cron.get_next_fire_time(None, now_iso_dt())
            next_run_at = _isoformat(nxt) if nxt else None
        except Exception:  # pragma: no cover — defensive
            next_run_at = None
        await self._dao.record_run(
            job_id, last_run_at=now_iso(), next_run_at=next_run_at
        )
        logger.info(
            "fired job %s (source=%s) result=%s", job_id, source, result_text
        )

    async def _ensure_scheduled_session(self) -> str:
        """Return the system session id used as the parent of cron tasks."""
        if self._scheduled_session_id is not None:
            return self._scheduled_session_id
        # Look for an existing one first; we don't want to create a
        # new session on every fire.
        existing = await self._sessions.list(limit=200)
        for s in existing:
            if s.get("title") == _SCHEDULED_SESSION_NAME:
                self._scheduled_session_id = s["id"]
                return s["id"]
        # None found — create one.
        sid = f"{_SCHEDULED_SESSION_ID_PREFIX}{uuid.uuid4().hex[:8]}"
        await self._sessions.create(id=sid, title=_SCHEDULED_SESSION_NAME)
        self._scheduled_session_id = sid
        return sid

    # ------------------------------------------------------------------
    # Convenience for tests / IPC
    # ------------------------------------------------------------------

    def has_job(self, job_id: str) -> bool:
        """Return ``True`` if APScheduler is tracking ``job_id``."""
        return self._aps.get_job(_aps_id(job_id)) is not None

    def next_run_time(self, job_id: str) -> str | None:
        """Return ISO-formatted next fire time for ``job_id`` (or ``None``)."""
        job = self._aps.get_job(_aps_id(job_id))
        if job is None or job.next_run_time is None:
            return None
        return _isoformat(job.next_run_time)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _aps_id(job_id: str) -> str:
    return f"job_{job_id}"


def _parse_cron(cron_expr: str) -> str:
    """Validate a 5-field cron expression; return the next ISO fire time.

    Raises :class:`InvalidCronError` on bad input.
    """
    try:
        trigger = CronTrigger.from_crontab(cron_expr)
    except Exception as exc:
        raise InvalidCronError(f"invalid cron expression {cron_expr!r}: {exc}") from exc
    try:
        nxt = trigger.get_next_fire_time(None, now_iso_dt())
    except Exception as exc:
        raise InvalidCronError(
            f"could not compute next fire time for {cron_expr!r}: {exc}"
        ) from exc
    return _isoformat(nxt) if nxt else ""


def _isoformat(dt: Any) -> str:
    """Format a ``datetime`` to the storage-layer ISO-8601 string."""
    if dt is None:
        return ""
    # The storage layer uses UTC second-precision with a trailing 'Z'.
    from datetime import UTC

    return dt.astimezone(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def now_iso_dt() -> Any:
    """Timezone-aware UTC ``datetime`` for ``CronTrigger.get_next_fire_time``."""
    from datetime import UTC, datetime

    return datetime.now(UTC)


async def _noop_runner(payload: dict[str, Any]) -> dict[str, Any]:
    """Default payload runner — returns the payload unchanged.

    Tests / IPC smoke tests rely on this so they can assert on the
    "fired a noop" signal. The real runner is injected at app
    startup once the runtime is available.
    """
    return {"ok": True, "echo": payload}


def _run_payload_sync(
    runner: PayloadFn, payload: dict[str, Any]
) -> Any:
    """Adapt an async ``PayloadFn`` to a blocking executor call.

    The scheduler's thread-pool executor must call a sync callable,
    so we spin a private event loop here for the duration of the
    payload. This keeps the main asyncio loop free for IPC.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        # We're already on a loop — shouldn't happen because we route
        # through ``run_in_executor``, but be defensive.
        coro = runner(payload)
        return asyncio.run_coroutine_threadsafe(coro, loop).result()
    # No running loop: drive the coroutine on a private loop.
    return asyncio.run(runner(payload))


def _stringify_payload_result(result: Any) -> str:
    if result is None:
        return "ok"
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and "error" in result:
        return f"error: {result['error']}"
    try:
        import json

        return json.dumps(result, default=str)[:200]
    except Exception:
        return repr(result)[:200]


# ---------------------------------------------------------------------------
# Process-wide singleton
# ---------------------------------------------------------------------------


_SCHEDULER: JobScheduler | None = None
_SCHEDULER_LOCK = asyncio.Lock()


async def get_scheduler(db: AsyncDatabase | None = None) -> JobScheduler:
    """Return the process-wide :class:`JobScheduler`, building it lazily.

    The first caller passes an open :class:`AsyncDatabase`; subsequent
    callers ignore the argument and return the cached instance.
    """
    global _SCHEDULER
    if _SCHEDULER is not None:
        return _SCHEDULER
    async with _SCHEDULER_LOCK:
        if _SCHEDULER is not None:
            return _SCHEDULER
        if db is None:
            raise SchedulerError(
                "get_scheduler() called before scheduler was built "
                "(no AsyncDatabase provided)"
            )
        sched = JobScheduler(db)
        await sched.start()
        _SCHEDULER = sched
        return sched


def set_scheduler(scheduler: JobScheduler | None) -> None:
    """Replace the cached scheduler (used by tests that inject a fake)."""
    global _SCHEDULER
    _SCHEDULER = scheduler


__all__ = [
    "InvalidCronError",
    "JobScheduler",
    "PayloadFn",
    "SchedulerError",
    "get_scheduler",
    "set_scheduler",
]
