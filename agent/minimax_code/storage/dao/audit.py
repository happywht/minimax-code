"""DAO — audit log.

Every tool dispatch in ``AgentCore._dispatch_tool`` writes a row here.
The table is append-only — rows are never updated, only inserted and
(optionally) purged by an administrator.

The dominant access patterns are:

* "give me the latest N entries" (paginated list, ordered by time DESC)
* "how many entries per tool / per status?" (stats aggregation)
* "delete everything older than date X" (purge)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ._base import (
    apply_pagination,
    dumps_json,
    now_iso,
    row_to_dict,
)

logger = logging.getLogger(__name__)


class AuditLogDAO:
    """Async DAO for the ``audit_log`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def record(
        self,
        *,
        id: str | None = None,
        session_id: str | None = None,
        tool_name: str,
        tool_args: dict[str, Any] | str | None = None,
        permission: str | None = None,
        result_status: str,
        exit_code: int | None = None,
        duration_ms: int | None = None,
        error: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        """Insert an audit record and return the hydrated row."""
        row_id = id or f"aud_{uuid.uuid4().hex[:10]}"
        args_json: str | None = None
        if isinstance(tool_args, dict):
            args_json = dumps_json(tool_args)
        elif isinstance(tool_args, str):
            args_json = tool_args
        sql = (
            "INSERT INTO audit_log "
            "(id, session_id, tool_name, tool_args, permission, "
            "result_status, exit_code, duration_ms, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            row_id,
            session_id,
            tool_name,
            args_json,
            permission,
            result_status,
            exit_code,
            duration_ms,
            (error or "")[:2000] if error else None,
            created_at or now_iso(),
        )
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone(
            "SELECT * FROM audit_log WHERE id = ?", (row_id,)
        )
        return _hydrate(row)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_recent(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        tool_name: str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return audit entries ordered by time descending."""
        where, params = _filters(tool_name=tool_name, session_id=session_id)
        sql = f"SELECT * FROM audit_log {where} ORDER BY created_at DESC"
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def count(
        self,
        *,
        tool_name: str | None = None,
        session_id: str | None = None,
    ) -> int:
        where, params = _filters(tool_name=tool_name, session_id=session_id)
        row = await self._db.fetchone(
            f"SELECT COUNT(*) AS n FROM audit_log {where}", tuple(params)
        )
        return int(row["n"]) if row else 0

    async def stats(self) -> dict[str, Any]:
        """Aggregate stats: {total, by_tool: {name: count}, by_status: {status: count}}."""
        total_row = await self._db.fetchone("SELECT COUNT(*) AS n FROM audit_log")
        total = int(total_row["n"]) if total_row else 0

        by_tool: dict[str, int] = {}
        rows = await self._db.fetchall(
            "SELECT tool_name, COUNT(*) AS n FROM audit_log GROUP BY tool_name"
        )
        for r in rows:
            by_tool[r["tool_name"]] = int(r["n"])

        by_status: dict[str, int] = {}
        rows = await self._db.fetchall(
            "SELECT result_status, COUNT(*) AS n FROM audit_log GROUP BY result_status"
        )
        for r in rows:
            by_status[r["result_status"]] = int(r["n"])

        return {"total": total, "by_tool": by_tool, "by_status": by_status}

    # ------------------------------------------------------------------
    # Purge
    # ------------------------------------------------------------------

    async def purge_before(self, before_iso: str) -> int:
        """Delete all records older than ``before_iso``. Returns deleted count."""
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM audit_log WHERE created_at < ?", (before_iso,)
            )
            return cur.rowcount


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any]:
    d = row_to_dict(row)
    if d is None:
        return {"id": "unknown"}
    return d


def _filters(
    *,
    tool_name: str | None = None,
    session_id: str | None = None,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if tool_name is not None:
        clauses.append("tool_name = ?")
        params.append(tool_name)
    if session_id is not None:
        clauses.append("session_id = ?")
        params.append(session_id)
    if not clauses:
        return "", []
    return "WHERE " + " AND ".join(clauses), params


__all__ = ["AuditLogDAO"]
