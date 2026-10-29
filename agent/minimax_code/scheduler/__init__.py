"""Loop-owned Native prompt scheduling with durable SQLite occurrences.

APScheduler wakes the async dispatcher; persisted due cursors and job leases
control admission. Each occurrence owns its immutable context, session, task,
RunService run and descendants through terminal cleanup. Legacy payload helper
functions below are not production dispatch routes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from ..storage.dao._base import dumps_json, now_iso
from ..storage.dao.schedule_occurrences import ScheduleOccurrencesDAO
from ..storage.dao.scheduled_jobs import ScheduledJobsDAO
from ..storage.dao.sessions import SessionsDAO
from ..storage.dao.tasks import TasksDAO
from ..storage.db import AsyncDatabase

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Legacy helper constants; durable occurrence dispatch creates isolated sessions.
_SCHEDULED_SESSION_NAME = "scheduled-tasks"
_SCHEDULED_SESSION_ID_PREFIX = "ses_sched_"

#: Safety valve for a hung scheduled payload (seconds). Far above any
#: legitimate agent run, but stops one stuck payload from holding an
#: executor worker thread forever (v1.2.2).
_PAYLOAD_TIMEOUT_S = 3600.0


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
    payload_runner, executor:
        Retained for standalone legacy helper tests. Production occurrences
        always use the bound Native RunService path on the owning event loop.
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
        self._aps = AsyncIOScheduler(executors={"default": {"type": "asyncio"}})
        self._occurrences = ScheduleOccurrencesDAO(db)
        self._occurrence_tasks: dict[str, asyncio.Task] = {}
        self._payload_runner: PayloadFn = payload_runner or _noop_runner
        self._start_lock = asyncio.Lock()
        self._stop_generation = 0
        self._stops_pending = 0
        self._admission_lock = asyncio.Lock()
        self._started = False
        self._stopping = False
        self._aps_shutdown_issued = False
        # We need a stable mapping APScheduler job id -> our job id so
        # we can look up the row when a fire happens. APScheduler uses
        # arbitrary string keys; we set ours to ``job_<row_id>``.
        # Guarded by a lock because ``apscheduler`` itself is mostly
        # single-threaded but our wrapper methods might be called from
        # the IPC layer concurrently.
        self._id_lock = threading.Lock()
        self._scheduled_session_id: str | None = None
        # v1.2.2: strong references for fire-and-forget ``_fire`` tasks.
        # Bare ``loop.create_task`` results were never stored, so the
        # loop was free to garbage-collect a task mid-run (a known
        # asyncio pitfall) and any exception died unseen.
        self._inflight: set[asyncio.Task] = set()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the underlying APScheduler and register persisted jobs."""
        generation = self._stop_generation
        async with self._start_lock:
            if generation != self._stop_generation or self._stops_pending:
                return
            if self._started:
                return
            self._stopping = False
            if self._aps_shutdown_issued:
                await asyncio.sleep(0)
                self._aps = AsyncIOScheduler(executors={"default": {"type": "asyncio"}})
                self._aps_shutdown_issued = False
            if not self._aps.running:
                self._aps.start()
            self._started = True
            # Re-load every enabled job from the DB. We do this AFTER
            # the scheduler has started so the add_job calls have a
            # running backend.
            await self._reconcile_occurrences(dispatch_pending=True)
            await self._reload_from_db()
            await self._dispatch_due()

    async def stop(self, *, wait: bool = True) -> None:
        """Stop the scheduler. Safe to call multiple times."""
        self._stopping = True
        self._stop_generation += 1
        self._stops_pending += 1
        try:
            async with self._start_lock:
                if self._aps.running and not self._aps_shutdown_issued:
                    self._aps_shutdown_issued = True
                    self._aps.shutdown(wait=wait)
                self._started = False
                # Wait for claims and run admissions that entered before the fence.
                async with self._admission_lock:
                    pass
                if wait:
                    await asyncio.gather(*list(self._inflight), return_exceptions=True)
        finally:
            self._stops_pending -= 1

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
        context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new scheduled job in the DB and register it with APScheduler.

        Returns the persisted dict (with ``id``, ``next_run_at``, …).
        Raises :class:`InvalidCronError` if ``cron_expr`` is malformed.
        """
        from ..schedule_context import capture_context, validate_payload
        validate_payload(payload)
        saved = await capture_context(self._db, context or {})
        next_run = _next_due(cron_expr, saved["timezone"])
        row = await self._dao.create(name=name, cron_expr=cron_expr, payload=payload, enabled=False)
        await self._db.execute("UPDATE scheduled_jobs SET context=?,context_version=1,timezone=?,binding_state='ready',binding_reason=NULL,enabled=?,next_run_at=? WHERE id=?",
                               (dumps_json(saved), saved["timezone"], int(enabled), next_run, row["id"]))
        row = await self._dao.get(row["id"])
        if enabled and self._started:
            self._register_with_aps(row)
        return row

    async def update_job(self, job_id, *, expected_revision, name, cron_expr, payload, context):
        _require_revision(expected_revision)
        from ..schedule_context import capture_context, validate_payload
        validate_payload(payload)
        saved = await capture_context(self._db, context)
        next_due = _next_due(cron_expr, saved["timezone"])
        async with self._db.transaction() as conn:
            cursor = await conn.execute("UPDATE scheduled_jobs SET name=?,cron_expr=?,payload=?,context=?,context_version=1,timezone=?,revision=revision+1,binding_state='ready',binding_reason=NULL,next_run_at=? WHERE id=? AND revision=? AND deleted_at IS NULL",
                (name, cron_expr, dumps_json(payload), dumps_json(saved), saved["timezone"], next_due, job_id, expected_revision))
            if cursor.rowcount != 1:
                raise SchedulerError("Schedule revision conflict; refresh before editing")
            # A rebind may retire only work that has never been admitted.
            from ..storage.dao.schedule_occurrences import fetch
            active = await fetch(conn,"SELECT o.* FROM schedule_occurrences o JOIN scheduled_jobs j ON j.active_occurrence_id=o.id WHERE j.id=?",(job_id,))
            if active and not active["run_id"]:
                await conn.execute("UPDATE schedule_occurrences SET status='superseded',reason='Explicit schedule rebind before admission',completed_at=?,updated_at=? WHERE id=?",(now_iso(),now_iso(),active["id"]))
                await conn.execute("UPDATE tasks SET status='cancelled',completed_at=? WHERE id=?",(now_iso(),active["task_id"]))
                await conn.execute("UPDATE scheduled_jobs SET active_occurrence_id=NULL WHERE id=?",(job_id,))
        row = await self._dao.get(job_id)
        self._unregister_from_aps(job_id)
        if row["enabled"] and self._started:
            self._register_with_aps(row)
        return row

    async def remove_job(self, job_id: str) -> bool:
        """Delete a job. Returns ``True`` if a row was removed."""
        # Unregister first so we never fire a row that was just deleted.
        self._unregister_from_aps(job_id)
        return await self._dao.delete(job_id)

    async def enable(self, job_id: str, *, acknowledge_occurrence_id=None) -> dict[str, Any] | None:
        from ..schedule_context import validate_context, validate_payload
        await self._reconcile_occurrences()
        row = await self._dao.get(job_id)
        if row is None or row.get("deleted_at"):
            return None
        validate_payload(row.get("payload"))
        if row.get("binding_state") == "needs_rebind":
            raise SchedulerError("Legacy schedule requires explicit rebind")
        await validate_context(self._db, row.get("context") or {})
        active = await self._occurrences.get(row["active_occurrence_id"]) if row.get("active_occurrence_id") else None
        if active and active["run_id"]:
            from ..storage.dao.runs import AgentRunsDAO
            run = await AgentRunsDAO(self._db).get_run(active["run_id"])
            from ..runs import get_run_service
            if run and (run["status"] in {"queued","planning","running","awaiting_approval","waiting_input"}
                        or (_IPC_SERVER and get_run_service(_IPC_SERVER).get_job(run["id"]))):
                raise SchedulerError("Active occurrence still owns execution/cleanup")
            if acknowledge_occurrence_id != active["id"]:
                raise SchedulerError("Acknowledge the exact prior occurrence after reviewing its effects")
        async with self._db.transaction() as conn:
            cursor = await conn.execute("UPDATE scheduled_jobs SET enabled=1,binding_state='ready',binding_reason=NULL,next_run_at=?,active_occurrence_id=? WHERE id=? AND revision=? AND active_occurrence_id IS ?",
                (_next_due(row["cron_expr"],row["timezone"]), active["id"] if active and not active["run_id"] else None, job_id,row["revision"],row["active_occurrence_id"]))
            if cursor.rowcount != 1:
                raise SchedulerError("Schedule changed while enabling; refresh")
        row = await self._dao.get(job_id)
        if self._started:
            self._register_with_aps(row)
        if active and not active["run_id"]:
            self._spawn_occurrence(active)
        return row

    async def disable(self, job_id: str) -> dict[str, Any] | None:
        self._unregister_from_aps(job_id)
        return await self._dao.disable(job_id)

    async def list_jobs(self) -> list[dict[str, Any]]:
        """Return every job (enabled or not), as stored in the DB."""
        await self._reconcile_occurrences()
        return await self._dao.list_all()

    async def run_now(self, job_id: str, *, idempotency_key: str, expected_revision: int):
        _require_revision(expected_revision)
        if not isinstance(idempotency_key,str) or not idempotency_key.strip() or len(idempotency_key)>128:
            raise SchedulerError("A stable client idempotency_key is required")
        try:
            async with self._admission_lock:
                occurrence, created = await self._occurrences.claim(job_id, source="manual", manual_key=idempotency_key, expected_revision=expected_revision, allow_new=not self._stopping)
        except ValueError as exc:
            raise SchedulerError(str(exc)) from exc
        owner = await self._dao.get(job_id)
        if occurrence["status"] == "pending" and owner and owner["enabled"] and not owner.get("deleted_at") and owner["revision"]==occurrence["revision"]:
            self._spawn_occurrence(occurrence)
        return {**{key:occurrence[key] for key in ("id","task_id","run_id","status")},
                "occurrence_id": occurrence["id"],"created":created,"job_id":job_id,"triggered_at":occurrence["created_at"]}

    def _spawn_occurrence(self, occurrence):
        oid = occurrence["id"]
        if self._stopping:
            return
        if oid in self._occurrence_tasks:
            return
        task = asyncio.create_task(self._execute_occurrence(occurrence), name=f"schedule:{oid}")
        self._occurrence_tasks[oid] = task
        self._inflight.add(task)
        def done(t):
            self._inflight.discard(t)
            self._occurrence_tasks.pop(oid,None)
            if not t.cancelled() and t.exception():
                logger.error("Occurrence %s dispatcher failed",oid,exc_info=t.exception())
        task.add_done_callback(done)

    async def _settle_occurrence(self, occurrence_id, status, **kwargs):
        entry = await self._occurrences.settle(occurrence_id, status, **kwargs)
        if entry and _IPC_SERVER is not None:
            try:
                _IPC_SERVER.notify({"event": "notification.new", "data": entry})
            except Exception:
                logger.exception("Could not push scheduled occurrence notification")

    async def _reconcile_occurrences(self, *, dispatch_pending=False):
        from ..runs import get_run_service
        from ..storage.dao.runs import AgentRunsDAO
        from ..verification import task_outcome
        dao = AgentRunsDAO(self._db)
        for occurrence in await self._occurrences.pending():
            if occurrence["id"] in self._occurrence_tasks:
                continue
            run = await dao.get_run(occurrence["run_id"]) if occurrence["run_id"] else None
            if run:
                if _IPC_SERVER and get_run_service(_IPC_SERVER).get_job(run["id"]):
                    continue
                receipt = await dao.result_payload(run["id"]) or {}
                status = task_outcome({**receipt,"run_id":run["id"],"run_status":run["status"],"verification":(run.get("metadata") or {}).get("verification")})
                if status == "running":
                    continue
                await self._settle_occurrence(occurrence["id"],status,expected_run_id=run["id"],reason=run.get("error"),result=receipt.get("text"))
            elif dispatch_pending and occurrence["status"] == "pending":
                owner = await self._dao.get(occurrence["job_id"])
                if owner and owner["enabled"] and not owner.get("deleted_at") and owner["revision"]==occurrence["revision"]:
                    self._spawn_occurrence(occurrence)

    async def _dispatch_due(self):
        if self._stopping:
            return
        await self._reconcile_occurrences(dispatch_pending=True)
        for row in await self._dao.due():
            if not row.get("deleted_at") and row.get("binding_state") == "ready" and not row.get("active_occurrence_id"):
                self._spawn_fire(row,source="cron")

    # ------------------------------------------------------------------
    # Internal: APScheduler plumbing
    # ------------------------------------------------------------------

    def _spawn_fire(self, row: dict[str, Any], *, source: str) -> None:
        """Launch ``_fire`` as a tracked, crash-logging background task."""
        if self._stopping:
            return
        task = asyncio.get_running_loop().create_task(self._fire(row, source=source))
        self._inflight.add(task)

        def _done(t: asyncio.Task) -> None:
            self._inflight.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                logger.error(
                    "scheduler fire for job %s crashed",
                    row.get("id"),
                    exc_info=exc,
                )

        task.add_done_callback(_done)

    def _register_with_aps(self, row: dict[str, Any]) -> None:
        job_id = row["id"]
        aps_id = _aps_id(job_id)
        # Replace any existing job with the same id.
        if self._aps.get_job(aps_id) is not None:
            self._aps.remove_job(aps_id)

        async def _tick():
            if self._stopping:
                return
            task = asyncio.current_task()
            self._inflight.add(task)
            try:
                await self._dispatch_due()
            finally:
                self._inflight.discard(task)

        try:
            self._aps.add_job(
                _tick,
                CronTrigger.from_crontab(row["cron_expr"], timezone=row.get("timezone") or "Asia/Shanghai"),
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

    async def _run_prompt_via_runservice(
        self, payload: dict[str, Any], *, prompt: str, session_id: str, task_id: str | None = None, occurrence=None
    ) -> dict[str, Any]:
        """Run one scheduled prompt through the FULL native agent loop.

        M5 — this is the RunService path the planning doc demanded:
        tools, permissions, budget and durable run rows all behave
        exactly like a chat turn. Unattended consents time out denied —
        an unattended job never widens its own permissions. The result
        dict matches the payload-runner contract so the task row
        bookkeeping below is unchanged.
        """
        from ..ipc.builtins import handle_agent_send_message
        from ..ipc.server import Context

        server = _IPC_SERVER
        if server is None:
            return {"ok": False, "error": "scheduler has no IPC server bound; "
                    "set it via set_scheduler_ipc_server()"}

        class _SchedulerCtx(Context):
            """Synthetic per-fire context: replies captured, emits fanned
            out to the live listeners (a UI open at the time sees the
            stream) but never block the fire."""

            def __init__(self) -> None:
                self.server = server
                self.last_result: dict[str, Any] = {}
                self.last_error: dict[str, Any] | None = None

            async def reply(self, result: Any) -> None:
                self.last_result = result if isinstance(result, dict) else {"text": str(result)}

            async def reply_error(self, code: Any, message: str, data: Any = None) -> None:
                self.last_error = {"code": code, "message": message}

            async def emit(self, event: str, data: Any = None, **kwargs: Any) -> None:
                await super().emit(event, data, **kwargs)

        ctx = _SchedulerCtx()
        params: dict[str, Any] = {
            "session_id": session_id,
            "content": prompt,
            "idempotency_key": f"schedule:{occurrence['id']}",
            "mode": occurrence["context"]["mode"],
            "budget_tokens": occurrence["context"]["budget_tokens"],
        }
        if payload.get("project_id"):
            params["project_id"] = str(payload["project_id"])
        budget = payload.get("budget_tokens")
        if isinstance(budget, int) and budget > 0:
            params["budget_tokens"] = budget
        try:
            async with self._admission_lock:
                if self._stopping:
                    return {"deferred": True}
                await handle_agent_send_message(params, ctx, wait=False, internal_schedule={"context":occurrence["context"],"occurrence_id":occurrence["id"],"job_id":occurrence["job_id"],"revision":occurrence["revision"]})
        except Exception as exc:  # noqa: BLE001 — scheduler must survive
            logger.exception("runservice prompt fire failed")
            return {"ok": False, "error": f"runservice fire failed: {exc}"}
        if ctx.last_error is not None:
            return {"ok": False, "error": ctx.last_error.get("message", "run failed"),
                    "detail": ctx.last_error}
        result = dict(ctx.last_result)
        if result.get("run"):
            from ..app import get_db
            from ..runs import get_run_service
            from ..storage.dao.runs import AgentRunsDAO
            accepted = result["run"]
            dao, service = AgentRunsDAO(get_db()), get_run_service(server)
            job = await service.job_for_run(accepted["id"])
            if task_id:
                try:
                    await self._tasks.link_run(task_id, accepted["id"])
                except Exception as exc:
                    # Admission owns real work. Failed bookkeeping must never
                    # abandon a writer behind an unlinked task row.
                    service.cancel_run(accepted["id"])
                    if job is not None:
                        await asyncio.gather(service.wait(job), return_exceptions=True)
                    return {"ok": False, "run_id": accepted["id"], "run_status": "failed",
                            "error": f"Task linkage failed for run {accepted['id']}: {exc}",
                            "text": (await dao.result_payload(accepted["id"]) or {}).get("text", "")}
                try:
                    await ctx.emit("task.progress", {"task_id": task_id, "progress": .1,
                        "status": "running", "run_id": accepted["id"]})
                except Exception:
                    logger.exception("task linkage notification failed; execution remains owned")
            if job is not None:
                await asyncio.gather(service.wait(job), return_exceptions=True)
            run = await dao.get_run(accepted["id"])
            result = {**(await dao.result_payload(accepted["id"]) or {}),
                      "run_id": accepted["id"], "run_status": run["status"],
                      "verification": (run.get("metadata") or {}).get("verification")}
            if run.get("error"):
                result["error"] = run["error"]
        from ..verification import task_outcome
        result["ok"] = task_outcome(result) == "completed"
        result["output"] = result.get("text", "")
        result["run_id"] = result.get("run_id")
        # The durable run row is the real evidence; the task row keeps a
        # pointer rather than duplicating the whole transcript.
        result["evidence"] = {"kind": "run", "run_id": result.get("run_id")}
        return result


    async def _fire(self, row: dict[str, Any], *, source: str) -> None:
        if self._stopping:
            return
        if source != "cron":
            raise SchedulerError("Manual fires require run_now with a stable client key")
        due = row.get("next_run_at")
        if not due:
            return
        try:
            async with self._admission_lock:
                occurrence, created = await self._occurrences.claim(row["id"],source="cron",expected_revision=row["revision"],
                    scheduled_for=due,coalesced_through=now_iso(),next_due=_next_due(row["cron_expr"],row["timezone"]), allow_new=not self._stopping)
        except ValueError:
            return
        if created:
            self._spawn_occurrence(occurrence)

    async def _execute_occurrence(self, occurrence):
        from ..schedule_context import validate_context, validate_payload
        from ..verification import task_outcome
        try:
            if self._stopping:
                return
            owner = await self._dao.get(occurrence["job_id"])
            current = await self._occurrences.get(occurrence["id"])
            if (not current or current["status"] not in {"pending","blocked"} or not owner
                    or not owner["enabled"] or owner.get("deleted_at") or owner["revision"]!=occurrence["revision"]):
                return
            validate_payload({"prompt":occurrence["prompt"]})
            await validate_context(self._db,occurrence["context"])
            result = await self._run_prompt_via_runservice({"prompt":occurrence["prompt"]},prompt=occurrence["prompt"],
                session_id=occurrence["session_id"],task_id=occurrence["task_id"],occurrence=occurrence)
            if result.get("deferred"):
                return
            current = await self._occurrences.get(occurrence["id"])
            owner = await self._dao.get(occurrence["job_id"])
            if not current["run_id"] and (current["status"]=="superseded" or not owner["enabled"] or owner.get("deleted_at")):
                return
            # Admission/config failure before any model call remains repairable.
            status = task_outcome(result) if result.get("run_id") else "blocked"
            await self._settle_occurrence(occurrence["id"],status,expected_run_id=result.get("run_id"),reason=str(result.get("error")) if result.get("error") else None,result=result.get("text") or result.get("output"))
        except Exception as exc:
            current = await self._occurrences.get(occurrence["id"])
            if current and (current["status"] in {"superseded","cancelled"} or current["run_id"] != occurrence.get("run_id")):
                return
            logger.exception("Scheduled occurrence blocked")
            await self._settle_occurrence(occurrence["id"],"blocked",expected_run_id=current["run_id"] if current else None,reason=str(exc))
        finally:
            # A run-completed event can precede occurrence settlement. Wake
            # clients after this dispatcher has finished its bookkeeping.
            if _IPC_SERVER is not None:
                try:
                    from ..ipc.server import Context
                    await Context(server=_IPC_SERVER, method="schedule").emit(
                        "schedule.updated", {"job_id": occurrence["job_id"], "occurrence_id": occurrence["id"]})
                except Exception:
                    logger.exception("Schedule update notification failed")
            # A completed occurrence releases the DB lease only after RunService
            # has drained writer/validation/cleanup; then one overdue catch-up.
            if self._started and not self._stopping:
                task = asyncio.create_task(self._dispatch_due(), name="schedule-catchup")
                self._inflight.add(task)
                task.add_done_callback(self._inflight.discard)

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
        trigger = CronTrigger.from_crontab(cron_expr, timezone=_scheduler_timezone())
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


_IPC_SERVER: Any = None


def set_scheduler_ipc_server(server: Any) -> None:
    """Bind the process IPC server so prompt fires can run through the
    RunService (M5). Called once at app startup."""
    global _IPC_SERVER
    _IPC_SERVER = server


def _scheduler_timezone() -> str:
    """M5 — cron timezone. Defaults to Asia/Shanghai per the planning doc."""
    import os as _os

    return _os.environ.get("MINIMAX_CODE_SCHEDULER_TIMEZONE", "Asia/Shanghai")


async def _noop_runner(payload: dict[str, Any]) -> dict[str, Any]:
    """Default payload runner — returns the payload unchanged.

    Tests / IPC smoke tests rely on this so they can assert on the
    "fired a noop" signal. The real runner is injected at app
    startup once the runtime is available.
    """
    return {"ok": True, "echo": payload}


def _run_payload_sync(
    runner: PayloadFn,
    payload: dict[str, Any],
    main_loop: Any = None,
) -> Any:
    """Adapt an async ``PayloadFn`` to a blocking executor call.

    v1.2.2: the payload now runs on the agent's *main* event loop via
    ``run_coroutine_threadsafe`` — the previous ``asyncio.run`` fallback
    spun a private loop, so any main-loop-bound resource (aiosqlite
    connections, locks) touched by the runner blew up with "attached to
    a different loop". The executor thread blocks on the concurrent
    future until the payload finishes; a generous safety timeout
    cancels a hung payload instead of leaking the worker thread forever.
    """
    loop = main_loop
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
    if loop is not None and loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(runner(payload), loop)
        try:
            return fut.result(timeout=_PAYLOAD_TIMEOUT_S)
        except TimeoutError:
            # Safety valve: cancel the coroutine on the main loop and
            # surface the timeout to ``_fire``'s failure branch. On a
            # finished future ``cancel()`` is a no-op, so a legitimate
            # TimeoutError raised *by* the payload propagates as-is.
            fut.cancel()
            raise
    # No usable loop (direct sync call, e.g. unit tests): private-loop
    # fallback keeps the old behaviour.
    return asyncio.run(runner(payload))


def _stringify_payload_result(result: Any) -> str:
    if result is None:
        return "ok"
    if isinstance(result, str):
        return result
    if isinstance(result, dict) and "error" in result:
        return f"error: {result['error']}"
    # A dict carrying a non-empty ``output`` string (the scheduled-prompt
    # runner) means the text IS the result — store it verbatim instead of
    # a truncated JSON repr of the envelope around it.
    if isinstance(result, dict):
        output = result.get("output")
        if isinstance(output, str) and output:
            return output
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
_SCHEDULER_QUIESCING = False


def _build_default_payload_runner() -> PayloadFn | None:
    """Legacy standalone utility; never installed in production scheduling."""
    try:
        from ..agent.self_evolution import build_payload_runner

        return build_payload_runner(os.getcwd())
    except Exception:
        logger.debug(
            "legacy self-evolution helper unavailable",
            exc_info=True,
        )
        return None


async def get_scheduler(db: AsyncDatabase | None = None) -> JobScheduler:
    """Return the process-wide :class:`JobScheduler`, building it lazily.

    The first caller passes an open :class:`AsyncDatabase`; subsequent
    callers ignore the argument and return the cached instance. The
    Production dispatch accepts only fixed-context Native prompt occurrences.
    """
    global _SCHEDULER
    if _SCHEDULER_QUIESCING:
        raise SchedulerError("Scheduler is stopping")
    if _SCHEDULER is not None:
        return _SCHEDULER
    async with _SCHEDULER_LOCK:
        if _SCHEDULER_QUIESCING:
            raise SchedulerError("Scheduler is stopping")
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
        if _SCHEDULER_QUIESCING:
            await sched.stop(wait=False)
            raise SchedulerError("Scheduler is stopping")
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


def _next_due(cron_expr, timezone, now=None):
    from datetime import UTC, datetime, timedelta
    from zoneinfo import ZoneInfo
    current = now or datetime.now(UTC)
    try:
        trigger = CronTrigger.from_crontab(cron_expr,timezone=ZoneInfo(timezone))
        result = trigger.get_next_fire_time(None,current + timedelta(microseconds=1))
    except Exception as exc:
        raise InvalidCronError(f"invalid cron: {exc}") from exc
    return result.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00","Z") if result else None


def _require_revision(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SchedulerError("expected_revision must be a positive integer")


async def quiesce_scheduler(*, wait=False):
    """Shutdown boundary: do not lazily create a scheduler while closing."""
    global _SCHEDULER_QUIESCING
    _SCHEDULER_QUIESCING = True
    async with _SCHEDULER_LOCK:
        if _SCHEDULER is not None:
            await _SCHEDULER.stop(wait=wait)
