"""JSON-RPC handlers for the ``task.*`` namespace.

The task namespace exposes the durable progress ledger the
:class:`~minimax_code.progress.ProgressTracker` writes to.

Read-side methods
-----------------

* ``task.list``     — enumeration with optional
  ``session_id`` / ``status`` filters.
* ``task.get``      — full row by id (for the detail view).

Write-side methods
------------------

* ``task.start``    — create a new task and emit the
  ``started`` event.
* ``task.update``   — push a progress value and emit the
  ``progress`` event.
* ``task.complete`` — close the task as ``completed`` or
  ``failed`` and emit the terminal event.
* ``task.cancel``   — soft-cancel a running task (sets status to
  ``cancelled`` and emits the terminal event).

The handlers do *not* need an LLM client or a tool registry — they
are pure storage reads / writes plus ``agent.status`` events. The
DB handle comes from the process-wide :class:`ProgressTracker`
singleton wired up by
:func:`minimax_code.app.register_app_handlers`.
"""

from __future__ import annotations

import logging
from typing import Any

from .handler_utils import HandlerError, check_params
from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_task_handlers(server: Any, *, tracker: Any = None) -> None:
    """Register ``task.list`` / ``task.get`` / ``task.cancel`` on ``server``.

    Parameters
    ----------
    server:
        The :class:`~minimax_code.ipc.server.IPCServer` instance.
    tracker:
        Optional pre-built :class:`ProgressTracker`. When ``None``,
        handlers resolve the singleton via
        :func:`minimax_code.app.get_progress_tracker` — which
        itself may be ``None`` if the storage layer failed to
        open at boot. Handlers return a clear ``-32603`` error
        in that case so the UI can show a "storage unavailable"
        banner.
    """

    async def _get_tracker() -> Any:
        """Resolve the tracker — explicit arg first, then the singleton.

        If the singleton is missing, we attempt to open the DB
        on demand (the same path the ``skill.*`` handlers take
        via :func:`minimax_code.app.init_runtime`). This means
        a brand-new agent process can answer ``task.*`` calls
        without having gone through ``init_runtime`` first.
        """
        if tracker is not None:
            return tracker
        # Lazy import: avoid a circular dep at module load time.
        from ..app import get_progress_tracker, init_runtime

        existing = get_progress_tracker()
        if existing is not None:
            return existing
        # Try to bring up the runtime — that opens the DB and
        # populates the tracker singleton as a side effect.
        try:
            await init_runtime()
        except Exception:
            pass
        return get_progress_tracker()

    async def handle_task_list(params: Any, ctx: Context) -> None:
        try:
            tk = await _get_tracker()
            if tk is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "storage layer is not available; task tracking disabled",
                )
            check_params(params, expected_keys=set())
            session_id = (params or {}).get("session_id")
            status = (params or {}).get("status")
            tasks = await tk.list(
                session_id=session_id if session_id else None,
                status=status if status else None,
            )
            await ctx.reply({"tasks": tasks})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("task.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "task.list failed")

    async def handle_task_get(params: Any, ctx: Context) -> None:
        try:
            tk = await _get_tracker()
            if tk is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "storage layer is not available; task tracking disabled",
                )
            check_params(params, expected_keys={"task_id"})
            task_id = str(params["task_id"])
            task = await tk.get(task_id)
            if task is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown task_id: {task_id!r}"
                )
                return
            await ctx.reply({"task": task})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("task.get failed")
            await ctx.reply_error(INTERNAL_ERROR, "task.get failed")

    async def handle_task_start(params: Any, ctx: Context) -> None:
        """``task.start`` — create a new task and emit the ``started`` event.

        Params: ``{"session_id": "...", "title": "..."}``
        Reply:  ``{"task_id": "task_..."}``
        """
        try:
            tk = await _get_tracker()
            if tk is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "storage layer is not available; task tracking disabled",
                )
            check_params(params, expected_keys={"session_id", "title"})
            session_id = str(params["session_id"])
            title = str(params["title"])
            # Pre-flight: confirm the session row exists. This
            # lets us return a friendlier "session not found"
            # error than the raw FK constraint failure — and
            # also gives the smoke test a clear signal when
            # pre-seeding didn't take.
            existing = await tk.get_session(session_id)
            if existing is None:
                await ctx.reply_error(
                    INVALID_PARAMS,
                    f"unknown session_id: {session_id!r} (no such session)",
                )
                return
            task_id = await tk.start_task(
                session_id, title, emit=ctx.emit
            )
            await ctx.reply({"task_id": task_id})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("task.start failed")
            await ctx.reply_error(INTERNAL_ERROR, "task.start failed")

    async def handle_task_update(params: Any, ctx: Context) -> None:
        """``task.update`` — push a progress value and emit a ``progress`` event.

        Params: ``{"task_id": "...", "progress": 0..100, "message"?: "..."}``
        Reply:  ``{"task": {...}}``
        """
        try:
            tk = await _get_tracker()
            if tk is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "storage layer is not available; task tracking disabled",
                )
            check_params(params, expected_keys={"task_id", "progress"})
            task_id = str(params["task_id"])
            progress = int(params["progress"])
            message = params.get("message")
            task = await tk.update(
                task_id, progress, message=message, emit=ctx.emit
            )
            if task is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown task_id: {task_id!r}"
                )
                return
            await ctx.reply({"task": task})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except (TypeError, ValueError) as exc:
            await ctx.reply_error(
                INVALID_PARAMS, f"invalid progress value: {exc}"
            )
        except Exception:  # pragma: no cover — defensive
            logger.exception("task.update failed")
            await ctx.reply_error(INTERNAL_ERROR, "task.update failed")

    async def handle_task_complete(params: Any, ctx: Context) -> None:
        """``task.complete`` — close a task and emit a terminal event.

        Params: ``{"task_id": "...", "success"?: true, "error"?: "..."}``
        Reply:  ``{"task": {...}}``
        """
        try:
            tk = await _get_tracker()
            if tk is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "storage layer is not available; task tracking disabled",
                )
            check_params(params, expected_keys={"task_id"})
            task_id = str(params["task_id"])
            success = bool(params.get("success", True))
            error = params.get("error")
            task = await tk.complete(
                task_id, success=success, error=error, emit=ctx.emit
            )
            if task is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown task_id: {task_id!r}"
                )
                return
            await ctx.reply({"task": task})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("task.complete failed")
            await ctx.reply_error(INTERNAL_ERROR, "task.complete failed")

    async def handle_task_cancel(params: Any, ctx: Context) -> None:
        try:
            tk = await _get_tracker()
            if tk is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "storage layer is not available; task tracking disabled",
                )
            check_params(params, expected_keys={"task_id"})
            task_id = str(params["task_id"])
            existing = await tk.get(task_id)
            if existing is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown task_id: {task_id!r}"
                )
                return
            # Cancellation only makes sense for active tasks; calling
            # cancel on a terminal task is a no-op + a log line.
            if existing["status"] in ("completed", "failed", "cancelled"):
                logger.info(
                    "task.cancel called on terminal task %s (status=%s)",
                    task_id,
                    existing["status"],
                )
                await ctx.reply({"ok": True, "task": existing, "noop": True})
                return
            updated = await tk.cancel(task_id, emit=ctx.emit)
            await ctx.reply({"ok": True, "task": updated})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("task.cancel failed")
            await ctx.reply_error(INTERNAL_ERROR, "task.cancel failed")

    server.register("task.list", handle_task_list)
    server.register("task.get", handle_task_get)
    server.register("task.start", handle_task_start)
    server.register("task.update", handle_task_update)
    server.register("task.complete", handle_task_complete)
    server.register("task.cancel", handle_task_cancel)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

__all__ = ["register_task_handlers"]
