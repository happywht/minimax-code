"""DAO — notifications.

CRUD operations for the ``notifications`` table.  Supports paginated
listing with filters (type, source, unread-only), mark-read (single
and bulk), and purge for housekeeping.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ._base import (
    apply_pagination,
    now_iso,
    row_to_dict,
)

logger = logging.getLogger(__name__)


class NotificationDAO:
    """Async DAO for the ``notifications`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        type: str,
        title: str,
        body: str = "",
        source: str | None = None,
        source_id: str | None = None,
        priority: int = 0,
    ) -> dict[str, Any]:
        row_id = f"ntf_{uuid.uuid4().hex[:10]}"
        now = now_iso()
        sql = (
            "INSERT INTO notifications "
            "(id, type, title, body, source, source_id, priority, "
            "read, created_at, read_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, NULL)"
        )
        params = (row_id, type, title, body, source, source_id, priority, now)
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone(
            "SELECT * FROM notifications WHERE id = ?", (row_id,)
        )
        return _hydrate(row)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, notification_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM notifications WHERE id = ?", (notification_id,)
        )
        return _hydrate(row) if row else None

    async def list_unread(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM notifications WHERE read = 0 ORDER BY created_at DESC"
        sql, params = apply_pagination(sql, [], limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def list_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        type: str | None = None,
        source: str | None = None,
        unread_only: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        """Return (entries, total_count) with optional filters."""
        where, params = _filters(type=type, source=source, unread_only=unread_only)
        count_sql = f"SELECT COUNT(*) AS n FROM notifications {where}"
        row = await self._db.fetchone(count_sql, tuple(params))
        total = int(row["n"]) if row else 0

        list_sql = f"SELECT * FROM notifications {where} ORDER BY created_at DESC"
        list_sql, params = apply_pagination(list_sql, list(params), limit=limit, offset=offset)
        rows = await self._db.fetchall(list_sql, tuple(params))
        return [_hydrate(r) for r in rows], total

    async def count_unread(self) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM notifications WHERE read = 0"
        )
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------
    # Update — mark read
    # ------------------------------------------------------------------

    async def mark_read(self, notification_id: str) -> dict[str, Any] | None:
        now = now_iso()
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE notifications SET read = 1, read_at = ? WHERE id = ?",
                (now, notification_id),
            )
        row = await self._db.fetchone(
            "SELECT * FROM notifications WHERE id = ?", (notification_id,)
        )
        return _hydrate(row) if row else None

    async def mark_all_read(self) -> int:
        now = now_iso()
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "UPDATE notifications SET read = 1, read_at = ? WHERE read = 0",
                (now,),
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete(self, notification_id: str) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM notifications WHERE id = ?", (notification_id,)
            )
            return cur.rowcount > 0

    async def purge(
        self,
        *,
        before_iso: str | None = None,
        read_only: bool = False,
    ) -> int:
        """Delete notifications matching the criteria.

        Parameters
        ----------
        before_iso:
            Only delete notifications created before this ISO-8601
            timestamp.  When ``None``, no time filter is applied.
        read_only:
            Only delete notifications that have been read.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if before_iso is not None:
            clauses.append("created_at < ?")
            params.append(before_iso)
        if read_only:
            clauses.append("read = 1")
        where = ""
        if clauses:
            where = "WHERE " + " AND ".join(clauses)
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                f"DELETE FROM notifications {where}", tuple(params)
            )
            return cur.rowcount


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any]:
    d = row_to_dict(row)
    if d is None:
        return {"id": "unknown"}
    # Normalise SQLite booleans.
    if "read" in d:
        d["read"] = bool(d["read"])
    return d


def _filters(
    *,
    type: str | None = None,
    source: str | None = None,
    unread_only: bool = False,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if type is not None:
        clauses.append("type = ?")
        params.append(type)
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    if unread_only:
        clauses.append("read = 0")
    if not clauses:
        return "", []
    return "WHERE " + " AND ".join(clauses), params


__all__ = ["NotificationDAO"]
