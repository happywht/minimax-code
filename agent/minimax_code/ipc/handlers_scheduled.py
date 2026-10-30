"""JSON-RPC handlers for the ``schedule.*`` namespace.

These wrap :class:`~minimax_code.scheduler.JobScheduler` so the
frontend can list / create / delete / enable / disable / trigger
cron jobs without having to spin up a separate background process.

Wire-up
-------
:func:`register_scheduled_handlers` is called from
:func:`minimax_code.app.register_app_handlers` after the
:class:`~minimax_code.storage.db.AsyncDatabase` has been migrated.
The scheduler is built lazily on the first ``schedule.*`` request.

The handlers each call :func:`_ensure_runtime` so a request before
the scheduler is ready (e.g. right at boot) does not crash — they
just get a "scheduler not ready" error envelope and can retry.

Schema
------
``schedule.list``     -> ``{ jobs: [...] }``
``schedule.create``   -> ``{ job: {...} }``
``schedule.delete``   -> ``{ ok: true }``
``schedule.enable``   -> ``{ job: {...} }``
``schedule.disable``  -> ``{ job: {...} }``
``schedule.run_now``  -> ``{ ok: true, run_id, job_id, triggered_at }``
"""

from __future__ import annotations

import logging
from typing import Any

from .handler_utils import HandlerError, check_params
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
)
from .server import Context

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_scheduled_handlers(server: Any, scheduler: Any = None) -> None:
    """Register the ``schedule.*`` handlers on ``server``.

    Parameters
    ----------
    server:
        The :class:`~minimax_code.ipc.server.IPCServer` instance.
    scheduler:
        Optional pre-built :class:`JobScheduler`. When ``None``,
        handlers build / look up the singleton via
        :func:`minimax_code.scheduler.get_scheduler` on each call.
    """
    scheduler_factory = _make_scheduler_factory(scheduler)

    async def handle_schedule_list(params: Any, ctx: Context) -> None:
        try:
            sched = await scheduler_factory()
            check_params(params, expected_keys=set())
            enabled_only = bool((params or {}).get("enabled_only", False)) if params else False
            jobs = await sched.list_jobs()
            if enabled_only:
                jobs = [j for j in jobs if j.get("enabled")]
            await ctx.reply({"jobs": jobs})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS,str(exc))
        except Exception:  # pragma: no cover — defensive
            logger.exception("schedule.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "schedule.list failed")

    async def handle_schedule_create(params: Any, ctx: Context) -> None:
        try:
            sched = await scheduler_factory()
            check_params(params, expected_keys={"name", "cron_expr"})
            if set(params) - {"name", "cron_expr", "payload", "enabled", "context", "expected_service_id", "idempotency_key"}:
                raise HandlerError(INVALID_PARAMS, "Unsupported schedule creation fields")
            expected_service = params.get("expected_service_id")
            if expected_service is not None:
                if not isinstance(expected_service, str) or not expected_service:
                    raise ValueError("Invalid expected service identity")
                from ..storage.dao.text_artifacts import ArtifactDAO
                if await ArtifactDAO(sched._db).service_id() != expected_service:
                    raise ValueError("Service identity changed; review the schedule again")
            name = str(params["name"]).strip()
            cron_expr = str(params["cron_expr"]).strip()
            payload = params.get("payload")
            if payload is not None and not isinstance(payload, dict):
                raise HandlerError(
                    INVALID_PARAMS, "payload must be a JSON object if provided"
                )
            enabled = params.get("enabled", True)
            if not isinstance(enabled, bool):
                raise ValueError("enabled must be a boolean")
            job = await sched.add_job(
                name=name,
                cron_expr=cron_expr,
                payload=payload,
                enabled=enabled,
                context=params.get("context") or {},
                idempotency_key=params.get("idempotency_key"),
                expected_service_id=expected_service,
            )
            replayed = job.pop("_create_replayed", None)
            await ctx.reply({"job": job, **({"created": not replayed} if replayed is not None else {})})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS,str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            msg = str(exc)
            # Surface "invalid cron" as a 4xx-style code so the UI can
            # show a useful error rather than the generic -32603.
            if "invalid cron" in msg.lower():
                await ctx.reply_error(INVALID_PARAMS, msg)
                return
            logger.exception("schedule.create failed")
            await ctx.reply_error(INTERNAL_ERROR, "schedule.create failed")

    async def handle_schedule_delete(params: Any, ctx: Context) -> None:
        try:
            sched = await scheduler_factory()
            check_params(params, expected_keys={"job_id"})
            job_id = str(params["job_id"])
            ok = await sched.remove_job(job_id)
            if not ok:
                raise HandlerError(INVALID_PARAMS, f"unknown job_id: {job_id!r}")
            await ctx.reply({"ok": True, "job_id": job_id})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS,str(exc))
        except Exception:  # pragma: no cover — defensive
            logger.exception("schedule.delete failed")
            await ctx.reply_error(INTERNAL_ERROR, "schedule.delete failed")

    async def handle_schedule_enable(params: Any, ctx: Context) -> None:
        try:
            sched = await scheduler_factory()
            check_params(params, expected_keys={"job_id"})
            job_id = str(params["job_id"])
            expected_service = params.get("expected_service_id")
            if expected_service is not None:
                if not isinstance(expected_service, str) or not expected_service:
                    raise ValueError("Invalid expected service identity")
                from ..storage.dao.text_artifacts import ArtifactDAO
                if await ArtifactDAO(sched._db).service_id() != expected_service:
                    raise ValueError("Service identity changed; review again")
            job = await sched.enable(job_id, acknowledge_occurrence_id=params.get("acknowledge_occurrence_id"),
                                     expected_revision=params.get("expected_revision"))
            if job is None:
                raise HandlerError(INVALID_PARAMS, f"unknown job_id: {job_id!r}")
            await ctx.reply({"job": job})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS,str(exc))
        except Exception:  # pragma: no cover — defensive
            logger.exception("schedule.enable failed")
            await ctx.reply_error(INTERNAL_ERROR, "schedule.enable failed")

    async def handle_schedule_disable(params: Any, ctx: Context) -> None:
        try:
            sched = await scheduler_factory()
            check_params(params, expected_keys={"job_id"})
            if set(params) - {"job_id", "expected_revision", "expected_service_id", "expected_occurrence_id"}:
                raise ValueError("Unsupported schedule disable fields")
            job_id = str(params["job_id"])
            job = await sched.disable(job_id, expected_revision=params.get("expected_revision"),
                                      expected_service_id=params.get("expected_service_id"),
                                      expected_occurrence_id=params.get("expected_occurrence_id"))
            if job is None:
                raise HandlerError(INVALID_PARAMS, f"unknown job_id: {job_id!r}")
            await ctx.reply({"job": job})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS,str(exc))
        except Exception as exc:
            from ..scheduler import SchedulerError
            if isinstance(exc, SchedulerError):
                await ctx.reply_error(INVALID_PARAMS, str(exc))
                return
            logger.exception("schedule.disable failed")
            await ctx.reply_error(INTERNAL_ERROR, "schedule.disable failed")

    async def handle_schedule_run_now(params: Any, ctx: Context) -> None:
        try:
            sched = await scheduler_factory()
            check_params(params, expected_keys={"job_id"})
            job_id = str(params["job_id"])
            if set(params) - {"job_id", "idempotency_key", "expected_revision"}:
                raise HandlerError(INVALID_PARAMS, "Unexpected manual trigger payload")
            # We don't await the fire — the cron task is fire-and-
            # forget. The IPC reply just confirms the kick. The
            # resulting ``tasks`` row is visible via ``task.list``.
            from ..scheduler import SchedulerError

            try:
                ack = await sched.run_now(job_id, idempotency_key=params.get("idempotency_key"), expected_revision=params.get("expected_revision"))
            except SchedulerError as exc:
                raise HandlerError(INVALID_PARAMS, str(exc)) from exc
            await ctx.reply({"ok": True, **ack})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS,str(exc))
        except Exception:  # pragma: no cover — defensive
            logger.exception("schedule.run_now failed")
            await ctx.reply_error(INTERNAL_ERROR, "schedule.run_now failed")

    async def handle_schedule_update(params, ctx):
        try:
            sched = await scheduler_factory()
            check_params(params, expected_keys={"job_id","expected_revision","name","cron_expr","payload","context"})
            if set(params) - {"job_id", "expected_revision", "name", "cron_expr", "payload", "context", "expected_service_id"}:
                raise ValueError("Unsupported schedule update fields")
            expected_service = params.get("expected_service_id")
            if expected_service is not None:
                if not isinstance(expected_service, str) or not expected_service:
                    raise ValueError("Invalid expected service identity")
                from ..storage.dao.text_artifacts import ArtifactDAO
                if await ArtifactDAO(sched._db).service_id() != expected_service:
                    raise ValueError("Service identity changed; review the schedule again")
            job = await sched.update_job(params["job_id"], expected_revision=params["expected_revision"], name=params["name"],
                cron_expr=params["cron_expr"],payload=params["payload"],context=params["context"])
            await ctx.reply({"job":job})
        except Exception as exc:
            await ctx.reply_error(INVALID_PARAMS,str(exc))

    async def handle_occurrences(params, ctx):
        try:
            sched = await scheduler_factory()
            params = params or {}
            before = params.get("before")
            status_group = params.get("status_group")
            if status_group is not None and status_group not in ("attention", "failed", "completed", "active", "other"):
                raise ValueError("Invalid occurrence status group")
            if before is not None and (not isinstance(before, dict) or
                    not isinstance(before.get("created_at"), str) or not before["created_at"] or
                    not isinstance(before.get("id"), str) or not before["id"]):
                raise ValueError("Invalid occurrence cursor")
            await sched._reconcile_occurrences()
            rows = await sched._occurrences.list(params.get("job_id"), limit=101, before=before, status_group=status_group)
            page = rows[:100]
            next_cursor = ({"created_at": page[-1]["created_at"], "id": page[-1]["id"]}
                           if len(rows) > 100 else None)
            await ctx.reply({"occurrences": page, "next_cursor": next_cursor})
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception as exc:
            await ctx.reply_error(INTERNAL_ERROR,str(exc))

    server.register("schedule.update", handle_schedule_update)
    server.register("schedule.occurrences", handle_occurrences)
    server.register("schedule.list", handle_schedule_list)
    server.register("schedule.create", handle_schedule_create)
    server.register("schedule.delete", handle_schedule_delete)
    server.register("schedule.enable", handle_schedule_enable)
    server.register("schedule.disable", handle_schedule_disable)
    server.register("schedule.run_now", handle_schedule_run_now)

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def _make_scheduler_factory(scheduler: Any | None) -> Any:
    """Return an async factory that yields a scheduler, building it lazily.

    * If ``scheduler`` is supplied (test path), the factory just
      returns it on every call.
    * Otherwise the factory looks up the process-wide singleton via
      :func:`minimax_code.scheduler.get_scheduler`. The first call
      builds it; subsequent calls return the cached instance.
    """
    if scheduler is not None:

        async def _factory() -> Any:
            return scheduler

        return _factory

    async def _factory() -> Any:
        # Lazy import: avoids a circular dependency at module
        # import time (handlers_scheduled is imported by app, not
        # the other way round).
        from ..app import ensure_db
        from ..scheduler import get_scheduler

        # If a scheduler has already been built by another code
        # path, use it. Otherwise build one now.
        try:
            sched = await get_scheduler()
        except Exception as exc:
            db = await ensure_db()
            if db is None:
                raise RuntimeError("storage is unavailable") from exc
            sched = await get_scheduler(db)
        return sched

    return _factory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

__all__ = ["register_scheduled_handlers"]
