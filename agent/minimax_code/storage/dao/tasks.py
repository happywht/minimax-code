"""DAO — task storage.

A *task* is a long-running unit of work the agent reports progress
on — "build the project", "run the test suite", "scan the repo".
Tasks live inside a session (FK cascade on delete) and are addressed
by the ``task.progress`` push event in the IPC contract.

Two surface layers
------------------

* :class:`TasksDAO` — the *low-level* CRUD used by tests and the
  scheduler. It takes an explicit ``id``, exposes pagination /
  ordering, and returns the full hydrated row.
* :class:`TaskDAO` — the *high-level* convenience layer used by the
  progress tracker and IPC handlers. It auto-generates IDs, uses a
  simpler ``create(session_id, title) -> task_id`` signature, and
  exposes ``update_progress`` / ``complete`` semantics the
  frontend can rely on.

Both classes share the same underlying DB handle and target the
same ``tasks`` table — they are siblings, not parent/child.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ._base import apply_pagination, now_iso, parse_order_by, row_to_dict

logger = logging.getLogger(__name__)


_SORTABLE: tuple[str, ...] = ("created_at", "started_at", "completed_at", "title", "id", "status")
_VALID_STATUS: frozenset[str] = frozenset(
    {"pending", "running", "completed", "failed", "cancelled"}
)


# ---------------------------------------------------------------------------
# Async DAO
# ---------------------------------------------------------------------------


class TasksDAO:
    """Async DAO for the ``tasks`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def create(
        self,
        *,
        id: str,
        session_id: str,
        title: str,
        status: str = "pending",
        progress: int = 0,
        created_at: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        if status not in _VALID_STATUS:
            raise ValueError(
                f"status must be one of {sorted(_VALID_STATUS)}, got {status!r}"
            )
        if not 0 <= progress <= 100:
            raise ValueError(f"progress must be in [0, 100], got {progress!r}")
        sql = (
            "INSERT INTO tasks "
            "(id, session_id, title, status, progress, created_at, "
            " started_at, completed_at, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            id,
            session_id,
            title,
            status,
            progress,
            created_at or now_iso(),
            started_at,
            completed_at,
            error,
        )
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone("SELECT * FROM tasks WHERE id = ?", (id,))
        return _hydrate(row)

    async def get(self, task_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return _hydrate(row)

    async def list(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        where, params = _build_filters(session_id=session_id, status=status)
        sql = f"SELECT * FROM tasks {where} ORDER BY {parse_order_by(order_by, _SORTABLE)}"
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def count(
        self, *, session_id: str | None = None, status: str | None = None
    ) -> int:
        where, params = _build_filters(session_id=session_id, status=status)
        row = await self._db.fetchone(
            f"SELECT COUNT(*) AS n FROM tasks {where}", tuple(params)
        )
        return int(row["n"]) if row else 0

    async def update_status(
        self,
        task_id: str,
        *,
        status: str,
        progress: int | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        if status not in _VALID_STATUS:
            raise ValueError(f"status must be one of {sorted(_VALID_STATUS)}")
        sets = ["status = ?"]
        params: list[Any] = [status]
        if progress is not None:
            if not 0 <= progress <= 100:
                raise ValueError(f"progress must be in [0, 100], got {progress!r}")
            sets.append("progress = ?")
            params.append(progress)
        if status == "running":
            # Auto-fill started_at the first time we transition to running.
            # Use COALESCE so a second "running" update doesn't clobber
            # the original start timestamp.
            sets.append("started_at = COALESCE(started_at, ?)")
            params.append(now_iso())
        if status in ("completed", "failed", "cancelled"):
            sets.append("completed_at = ?")
            params.append(now_iso())
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        params.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?"
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        return await self.get(task_id)

    async def update_progress(
        self, task_id: str, progress: int, *, message: str | None = None
    ) -> dict[str, Any] | None:
        """Set ``progress`` (and optionally the human-friendly status)."""
        return await self.update_status(
            task_id, status="running", progress=progress
        ) if False else await self._set_progress(task_id, progress)

    async def _set_progress(
        self, task_id: str, progress: int
    ) -> dict[str, Any] | None:
        if not 0 <= progress <= 100:
            raise ValueError(f"progress must be in [0, 100], got {progress!r}")
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE tasks SET progress = ? WHERE id = ?", (progress, task_id)
            )
        return await self.get(task_id)

    async def delete(self, task_id: str) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    return d  # int fields stay as-is; callers can interpret them


def _build_filters(
    *, session_id: str | None, status: str | None
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if clauses:
        return "WHERE " + " AND ".join(clauses), params
    return "", params


def create_sync(db, **fields) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    if fields.get("status", "pending") not in _VALID_STATUS:
        raise ValueError("invalid status")
    sql = (
        "INSERT INTO tasks "
        "(id, session_id, title, status, progress, created_at, "
        " started_at, completed_at, error) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    params = (
        fields["id"],
        fields["session_id"],
        fields["title"],
        fields.get("status", "pending"),
        fields.get("progress", 0),
        fields.get("created_at") or now_iso(),
        fields.get("started_at"),
        fields.get("completed_at"),
        fields.get("error"),
    )
    with db.transaction() as conn:
        conn.execute(sql, params)
    row = db.fetchone("SELECT * FROM tasks WHERE id = ?", (fields["id"],))
    return _hydrate(row)


__all__ = ["TasksDAO", "TaskDAO", "create_sync"]


# ---------------------------------------------------------------------------
# High-level DAO (progress tracker / IPC layer)
# ---------------------------------------------------------------------------


class TaskDAO:
    """High-level DAO for the ``tasks`` table.

    This is the convenience layer the progress tracker and the
    ``task.*`` IPC handlers use. Compared to :class:`TasksDAO`:

    * :meth:`create` auto-generates a UUID-like ``task_id`` and
      returns it directly (callers don't need to think about ids).
    * :meth:`update_progress` accepts the optional ``status`` and
      ``error`` fields in one call — the typical "the task is
      running and just hit 50%" update.
    * :meth:`complete` writes ``completed_at`` automatically and
      defaults the status to ``completed`` (pass ``status="failed"``
      to mark an errored task).
    * :meth:`list` returns a flat list of row dicts — no
      pagination/ordering knobs (use :class:`TasksDAO` for that).
    """

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    # ---- create / read ---------------------------------------------------

    async def create(self, session_id: str, title: str) -> str:
        """Insert a new ``pending`` task and return its generated id.

        The id is a short UUID hex prefixed with ``task_`` so it is
        distinguishable in logs and the UI. The row is created with
        ``status="pending"`` and ``progress=0``.
        """
        if not session_id or not isinstance(session_id, str):
            raise ValueError(f"session_id must be a non-empty string, got {session_id!r}")
        if not title or not isinstance(title, str):
            raise ValueError(f"title must be a non-empty string, got {title!r}")
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        sql = (
            "INSERT INTO tasks "
            "(id, session_id, title, status, progress, created_at, "
            " started_at, completed_at, error) "
            "VALUES (?, ?, ?, 'pending', 0, ?, NULL, NULL, NULL)"
        )
        async with self._db.transaction() as conn:
            await conn.execute(sql, (task_id, session_id, title, now_iso()))
        return task_id

    async def get(self, task_id: str) -> dict[str, Any] | None:
        """Return the row for ``task_id`` (or ``None`` if missing)."""
        row = await self._db.fetchone("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return row_to_dict(row)

    async def list(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return tasks matching the optional filters.

        Both filters are AND-ed. Results are ordered by
        ``created_at DESC`` so the most recent task is first — the
        order the UI typically renders.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            clauses.append("session_id = ?")
            params.append(session_id)
        if status is not None:
            if status not in _VALID_STATUS:
                raise ValueError(
                    f"status must be one of {sorted(_VALID_STATUS)}, got {status!r}"
                )
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM tasks {where} ORDER BY created_at DESC"
        rows = await self._db.fetchall(sql, tuple(params))
        return [row_to_dict(r) for r in rows]

    # ---- update ---------------------------------------------------------

    async def update_progress(
        self,
        task_id: str,
        progress: int,
        *,
        status: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        """Set ``progress`` (0-100) on a task.

        The default behaviour is to leave ``status`` alone — the
        caller can pass ``status="running"`` to bump a freshly
        created pending task into the running state in the same
        call. If ``status`` is one of the terminal values
        (``completed`` / ``failed`` / ``cancelled``) the row's
        ``completed_at`` timestamp is auto-filled.
        """
        if not 0 <= progress <= 100:
            raise ValueError(f"progress must be in [0, 100], got {progress!r}")
        if status is not None and status not in _VALID_STATUS:
            raise ValueError(
                f"status must be one of {sorted(_VALID_STATUS)}, got {status!r}"
            )

        sets: list[str] = ["progress = ?"]
        params: list[Any] = [progress]

        if status is not None:
            sets.append("status = ?")
            params.append(status)
            if status == "running":
                # COALESCE keeps the original started_at on re-entry.
                sets.append("started_at = COALESCE(started_at, ?)")
                params.append(now_iso())
            if status in ("completed", "failed", "cancelled"):
                sets.append("completed_at = ?")
                params.append(now_iso())
        if error is not None:
            sets.append("error = ?")
            params.append(error)

        params.append(task_id)
        sql = f"UPDATE tasks SET {', '.join(sets)} WHERE id = ?"
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        return await self.get(task_id)

    async def complete(
        self,
        task_id: str,
        *,
        status: str = "completed",
        error: str | None = None,
    ) -> dict[str, Any] | None:
        """Mark a task as done.

        ``status`` defaults to ``"completed"``; pass
        ``status="failed"`` (with ``error=``) for an errored task,
        or ``status="cancelled"`` for a user-cancelled one.
        ``completed_at`` is auto-filled.
        """
        if status not in ("completed", "failed", "cancelled"):
            raise ValueError(
                f"complete() status must be 'completed', 'failed' or 'cancelled', got {status!r}"
            )
        return await self.update_progress(
            task_id, progress=100, status=status, error=error
        )
