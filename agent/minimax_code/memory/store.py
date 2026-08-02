"""DAO for the ``memories`` table."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ..storage.dao._base import apply_pagination, now_iso, row_to_dict

logger = logging.getLogger(__name__)

_VALID_CATEGORIES: frozenset[str] = frozenset(
    {"preference", "decision", "lesson", "fact"}
)


class MemoriesDAO:
    """Async DAO for long-term memory rows."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def create(
        self,
        content: str,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
        category: str = "fact",
        confidence: float = 1.0,
        source: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new memory and return the persisted row."""
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")
        if category not in _VALID_CATEGORIES:
            raise ValueError(
                f"category must be one of {sorted(_VALID_CATEGORIES)}, got {category!r}"
            )
        if not isinstance(confidence, (int, float)) or not 0.0 <= confidence <= 1.0:
            raise ValueError(f"confidence must be in [0.0, 1.0], got {confidence!r}")

        memory_id = uuid.uuid4().hex
        now = now_iso()
        sql = (
            "INSERT INTO memories "
            "(id, project_id, session_id, content, category, confidence, source, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            memory_id,
            project_id,
            session_id,
            content.strip(),
            category,
            float(confidence),
            source,
            now,
            now,
        )
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone("SELECT * FROM memories WHERE id = ?", (memory_id,))
        return _hydrate(row)

    async def get(self, memory_id: str) -> dict[str, Any] | None:
        """Fetch a single memory by primary key."""
        row = await self._db.fetchone("SELECT * FROM memories WHERE id = ?", (memory_id,))
        return _hydrate(row)

    async def delete(self, memory_id: str) -> bool:
        """Hard-delete a memory. Returns ``True`` if a row was removed."""
        async with self._db.transaction() as conn:
            cur = await conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            return cur.rowcount > 0

    async def list(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
        category: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """List memories with optional filters and pagination."""
        where, params = _build_filters(
            project_id=project_id,
            session_id=session_id,
            category=category,
        )
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"SELECT * FROM memories {where_sql} ORDER BY updated_at DESC, id DESC"
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def count(
        self,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
        category: str | None = None,
        query: str | None = None,
    ) -> int:
        """Return the number of memories matching the given filters."""
        where, params = _build_filters(
            project_id=project_id,
            session_id=session_id,
            category=category,
            query=query,
        )
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        row = await self._db.fetchone(
            f"SELECT COUNT(*) AS n FROM memories {where_sql}", tuple(params)
        )
        return int(row["n"]) if row else 0

    async def search(
        self,
        query: str,
        *,
        project_id: str | None = None,
        session_id: str | None = None,
        category: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Substring search on ``content`` ordered by ``updated_at DESC``."""
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")

        where: list[str] = ["content LIKE ? COLLATE NOCASE"]
        params: list[Any] = [f"%{query.strip()}%"]
        if project_id is not None:
            where.append("project_id = ?")
            params.append(project_id)
        if session_id is not None:
            where.append("session_id = ?")
            params.append(session_id)
        if category is not None:
            where.append("category = ?")
            params.append(category)

        sql = (
            f"SELECT * FROM memories WHERE {' AND '.join(where)} "
            "ORDER BY updated_at DESC, id DESC"
        )
        sql, params = apply_pagination(sql, params, limit=limit, offset=0)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]


def _build_filters(
    *,
    project_id: str | None = None,
    session_id: str | None = None,
    category: str | None = None,
    query: str | None = None,
) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(project_id)
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if category is not None:
        clauses.append("category = ?")
        params.append(category)
    if query is not None:
        clauses.append("content LIKE ? COLLATE NOCASE")
        params.append(f"%{query}%")
    return clauses, params


def _hydrate(row: Any) -> dict[str, Any] | None:
    return row_to_dict(row)
