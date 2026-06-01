"""Progress tracking — durable, streamed progress for long-running tasks.

The :class:`ProgressTracker` is the seam between the ``tasks`` SQLite
table and the live ``agent.status`` push events the frontend
listens on. It is the single place that *both* writes the durable
row and emits the streaming event, so a UI subscriber and a
`SELECT * FROM tasks` query always agree on the current state.

Why a thin wrapper
------------------

The :class:`~minimax_code.storage.dao.tasks.TaskDAO` already does
the row-level work. The tracker adds three things on top:

1. **Auto-generated ids** are hidden from callers — they get a
   ``task_id`` string back, not a row dict.
2. **Event emission** — each state change pushes a typed
   ``agent.status`` event through whatever ``emit`` callable the
   caller supplies. The default is a no-op so the tracker is
   usable in unit tests and CLI tools that have no live IPC
   channel.
3. **Identity bookkeeping** — the in-memory ``_known`` set lets the
   tracker annotate emitted events with ``task_id`` (the DAO's
   row) without callers having to thread it through.

Usage from a request handler
----------------------------

The IPC layer wires the ``ctx.emit`` callable into the tracker on
each call so the stream lands in the *right* request envelope::

    tracker = get_progress_tracker()
    tid = await tracker.start_task(
        session_id, title, emit=ctx.emit
    )
    await tracker.update(tid, 50, message="halfway", emit=ctx.emit)
    await tracker.complete(tid, emit=ctx.emit)

The single global :data:`app._PROGRESS_TRACKER` is the
hand-off point — handlers grab it via :func:`get_tracker`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ..storage.dao.tasks import TaskDAO

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Emit callable type
# ---------------------------------------------------------------------------


class _SupportsEmit(Protocol):
    """Structural type for the subset of :class:`Context` we use.

    Defined as a Protocol so the tracker doesn't have to import
    :mod:`minimax_code.ipc.server` (which would create a circular
    import through :mod:`app`). Any object exposing
    ``async emit(event: str, data: Any) -> None`` matches.
    """

    async def emit(self, event: str, data: Any = None) -> None: ...


# ``emit`` is an async callable taking an event name + payload dict.
# Sync functions / ``None`` mean "do not emit" — the tracker falls
# back to a no-op in that case.
EmitFn = Callable[[str, dict[str, Any]], Awaitable[None]]


async def _no_emit(_event: str, _data: dict[str, Any]) -> None:
    """Default no-op emitter used when no IPC context is available."""
    return None


def _resolve_emit(emit: EmitFn | _SupportsEmit | None) -> EmitFn:
    """Coerce a ``ctx``-like / callable / ``None`` into a real emit fn."""
    if emit is None:
        return _no_emit
    if callable(emit):
        return emit  # type: ignore[return-value]
    # Object with an ``emit`` method (matches ``Context``).
    return emit.emit  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class ProgressTracker:
    """Wraps :class:`TaskDAO` and emits ``agent.status`` events.

    Parameters
    ----------
    dao:
        A :class:`~minimax_code.storage.dao.tasks.TaskDAO`
        instance (or anything with the same surface — kept
        untyped so tests can inject a stub).
    """

    #: Event name used for every progress push. The frontend
    #: ``ProgressPanel`` subscribes to this single channel.
    EVENT_NAME: str = "agent.status"

    def __init__(self, dao: Any) -> None:  # type: ignore[no-untyped-def]
        self._dao = dao

    # -- start / update / complete ----------------------------------------

    async def start_task(
        self,
        session_id: str,
        title: str,
        *,
        emit: EmitFn | _SupportsEmit | None = None,
    ) -> str:
        """Create a new ``pending`` task and announce it.

        Returns the generated ``task_id`` so the caller can pass
        it to subsequent :meth:`update` / :meth:`complete` calls.
        Emits an ``agent.status`` event with
        ``status="started"`` and a ``detail`` dict carrying the
        task id, session id, and title.
        """
        task_id = await self._dao.create(session_id=session_id, title=title)
        # Bump the row into "running" so the UI sees it as live
        # immediately. This also sets ``started_at``.
        await self._dao.update_progress(task_id, 0, status="running")
        emitter = _resolve_emit(emit)
        await emitter(
            self.EVENT_NAME,
            {
                "session_id": session_id,
                "task_id": task_id,
                "status": "started",
                "detail": {
                    "task_id": task_id,
                    "session_id": session_id,
                    "title": title,
                    "progress": 0,
                },
            },
        )
        return task_id

    async def update(
        self,
        task_id: str,
        progress: int,
        message: str | None = None,
        *,
        emit: EmitFn | _SupportsEmit | None = None,
    ) -> dict[str, Any] | None:
        """Push a progress update.

        Writes the new ``progress`` value (and auto-transitions to
        ``running`` if the row is still ``pending``) and emits an
        ``agent.status`` event with ``status="progress"`` and the
        numeric progress in the payload.
        """
        row = await self._dao.update_progress(
            task_id, progress, status="running"
        )
        emitter = _resolve_emit(emit)
        detail: dict[str, Any] = {
            "task_id": task_id,
            "progress": progress,
        }
        if message is not None:
            detail["message"] = message
        await emitter(
            self.EVENT_NAME,
            {
                "task_id": task_id,
                "status": "progress",
                "progress": progress,
                "detail": detail,
            },
        )
        return row

    async def complete(
        self,
        task_id: str,
        *,
        success: bool = True,
        error: str | None = None,
        emit: EmitFn | _SupportsEmit | None = None,
    ) -> dict[str, Any] | None:
        """Mark the task done.

        ``success=True`` (the default) writes ``status="completed"``;
        pass ``success=False`` to record ``status="failed"`` with
        the supplied ``error`` message. The terminal
        ``agent.status`` event has ``status="completed"`` or
        ``status="failed"`` accordingly.
        """
        status = "completed" if success else "failed"
        row = await self._dao.complete(task_id, status=status, error=error)
        emitter = _resolve_emit(emit)
        detail: dict[str, Any] = {"task_id": task_id, "progress": 100}
        if error is not None:
            detail["error"] = error
        await emitter(
            self.EVENT_NAME,
            {
                "task_id": task_id,
                "status": status,
                "progress": 100,
                "detail": detail,
            },
        )
        return row

    # -- passthroughs for IPC handlers ------------------------------------

    async def get(self, task_id: str) -> dict[str, Any] | None:
        """Return the task row by id (no event emitted)."""
        return await self._dao.get(task_id)

    async def list(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return tasks matching the optional filters (no event emitted)."""
        return await self._dao.list(session_id=session_id, status=status)

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Look up a session row by id.

        Used by the ``task.*`` handlers to validate the
        ``session_id`` argument before inserting a task (which
        would otherwise fail with a bare FK constraint error).
        Returns the row dict or ``None`` if missing.
        """
        db = self._dao._db  # type: ignore[attr-defined]
        row = await db.fetchone(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        if row is None:
            return None
        return {k: row[k] for k in row.keys()}

    async def cancel(
        self,
        task_id: str,
        *,
        emit: EmitFn | _SupportsEmit | None = None,
    ) -> dict[str, Any] | None:
        """Mark a task as ``cancelled`` and announce it.

        Distinct from :meth:`complete` because cancellation is a
        user-initiated terminal state — the emitted event uses
        ``status="cancelled"`` so the UI can render it as such
        (e.g. greyed out, "stopped by user") rather than as a
        normal success.
        """
        row = await self._dao.complete(task_id, status="cancelled")
        emitter = _resolve_emit(emit)
        await emitter(
            self.EVENT_NAME,
            {
                "task_id": task_id,
                "status": "cancelled",
                "progress": row["progress"] if row else 0,
                "detail": {"task_id": task_id},
            },
        )
        return row


__all__ = [
    "ProgressTracker",
    "EmitFn",
]
