"""DAO — task storage.

A *task* is a long-running unit of work the agent reports progress
on — "build the project", "run the test suite", "scan the repo".
Tasks live inside a session (FK cascade on delete) and are addressed
by the ``task.progress`` push event in the IPC contract.
"""

from __future__ import annotations

import logging
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


__all__ = ["TasksDAO", "create_sync"]
