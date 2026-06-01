"""DAO — scheduled jobs.

A *scheduled job* binds a cron expression to a JSON payload. The
scheduler (``minimax_code.scheduler.cron``) reads from this table
on startup and ticks every minute or so. The dominant access
pattern is "give me the next enabled job whose ``next_run_at`` is
in the past", which the composite index ``idx_jobs_enabled_next_run``
covers.
"""

from __future__ import annotations

import logging
from typing import Any

from ._base import (
    apply_pagination,
    dumps_json,
    loads_json,
    now_iso,
    parse_order_by,
    row_to_dict,
)

logger = logging.getLogger(__name__)


_SORTABLE: tuple[str, ...] = ("name", "next_run_at", "last_run_at", "created_at")


# ---------------------------------------------------------------------------
# Async DAO
# ---------------------------------------------------------------------------


class ScheduledJobsDAO:
    """Async DAO for the ``scheduled_jobs`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def create(
        self,
        *,
        id: str,
        name: str,
        cron_expr: str,
        payload: dict[str, Any] | None = None,
        enabled: bool = True,
        last_run_at: str | None = None,
        next_run_at: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        sql = (
            "INSERT INTO scheduled_jobs "
            "(id, name, cron_expr, payload, enabled, last_run_at, next_run_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            id,
            name,
            cron_expr,
            dumps_json(payload) if payload is not None else None,
            1 if enabled else 0,
            last_run_at,
            next_run_at,
            created_at or now_iso(),
        )
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone("SELECT * FROM scheduled_jobs WHERE id = ?", (id,))
        return _hydrate(row)

    async def get(self, job_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM scheduled_jobs WHERE id = ?", (job_id,)
        )
        return _hydrate(row)

    async def list(
        self,
        *,
        enabled: bool | None = None,
        limit: int | None = None,
        offset: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        where, params = _filters(enabled=enabled)
        sql = (
            f"SELECT * FROM scheduled_jobs {where} "
            f"ORDER BY {parse_order_by(order_by, _SORTABLE)}"
        )
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def due(self, *, now: str | None = None) -> list[dict[str, Any]]:
        """Return all enabled jobs whose ``next_run_at`` is at or before ``now``.

        Used by the scheduler tick — must stay cheap. Index
        ``idx_jobs_enabled_next_run`` covers this.
        """
        cutoff = now or now_iso()
        rows = await self._db.fetchall(
            "SELECT * FROM scheduled_jobs "
            "WHERE enabled = 1 AND next_run_at IS NOT NULL AND next_run_at <= ? "
            "ORDER BY next_run_at ASC",
            (cutoff,),
        )
        return [_hydrate(r) for r in rows]

    async def set_enabled(self, job_id: str, enabled: bool) -> dict[str, Any] | None:
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE scheduled_jobs SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, job_id),
            )
        return await self.get(job_id)

    async def record_run(
        self, job_id: str, *, last_run_at: str, next_run_at: str | None
    ) -> dict[str, Any] | None:
        """Update a job's ``last_run_at`` / ``next_run_at`` after a fire."""
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE scheduled_jobs SET last_run_at = ?, next_run_at = ? WHERE id = ?",
                (last_run_at, next_run_at, job_id),
            )
        return await self.get(job_id)

    async def delete(self, job_id: str) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM scheduled_jobs WHERE id = ?", (job_id,)
            )
            return cur.rowcount > 0

    async def count(self, *, enabled: bool | None = None) -> int:
        where, params = _filters(enabled=enabled)
        row = await self._db.fetchone(
            f"SELECT COUNT(*) AS n FROM scheduled_jobs {where}", tuple(params)
        )
        return int(row["n"]) if row else 0


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if d is None:
        return None
    d["payload"] = loads_json(d.get("payload"))
    d["enabled"] = bool(d.get("enabled", 0))
    return d


def _filters(*, enabled: bool | None) -> tuple[str, list[Any]]:
    if enabled is None:
        return "", []
    return "WHERE enabled = ?", [1 if enabled else 0]


def create_sync(db, **fields) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    sql = (
        "INSERT INTO scheduled_jobs "
        "(id, name, cron_expr, payload, enabled, last_run_at, next_run_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    params = (
        fields["id"],
        fields["name"],
        fields["cron_expr"],
        dumps_json(fields.get("payload")) if fields.get("payload") is not None else None,
        1 if fields.get("enabled", True) else 0,
        fields.get("last_run_at"),
        fields.get("next_run_at"),
        fields.get("created_at") or now_iso(),
    )
    with db.transaction() as conn:
        conn.execute(sql, params)
    row = db.fetchone("SELECT * FROM scheduled_jobs WHERE id = ?", (fields["id"],))
    return _hydrate(row)


def list_sync(
    db,  # type: ignore[no-untyped-def]
    *,
    enabled: bool | None = None,
) -> list[dict[str, Any]]:
    where, params = _filters(enabled=enabled)
    sql = f"SELECT * FROM scheduled_jobs {where} ORDER BY {parse_order_by(None, _SORTABLE)}"
    rows = db.fetchall(sql, tuple(params))
    return [_hydrate(r) for r in rows]


__all__ = [
    "ScheduledJobsDAO",
    "create_sync",
    "list_sync",
]
