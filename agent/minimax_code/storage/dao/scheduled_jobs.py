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
import uuid
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
        id: str | None = None,
        name: str,
        cron_expr: str,
        payload: dict[str, Any] | None = None,
        enabled: bool = True,
        last_run_at: str | None = None,
        next_run_at: str | None = None,
        created_at: str | None = None,
        bound_context: dict[str, Any] | None = None,
        request_key: str | None = None,
        request_hash: str | None = None,
        expected_service_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a scheduled job.

        ``id`` is auto-generated (``job_xxxxxxxx``) when not provided, so
        callers that don't care about the storage format can just pass
        ``name``, ``cron_expr``, and ``payload``.
        """
        row_id = id or f"job_{uuid.uuid4().hex[:10]}"
        columns = "id, name, cron_expr, payload, enabled, last_run_at, next_run_at, created_at"
        params: tuple[Any, ...] = (
            row_id,
            name,
            cron_expr,
            dumps_json(payload) if payload is not None else None,
            1 if enabled else 0,
            last_run_at,
            next_run_at,
            created_at or now_iso(),
        )
        if bound_context is not None:
            columns += ", context, context_version, timezone, binding_state, binding_reason"
            params += (dumps_json(bound_context), 1, bound_context["timezone"], "ready", None)
        sql = f"INSERT INTO scheduled_jobs ({columns}) VALUES ({', '.join('?' for _ in params)})"
        async with self._db.transaction() as conn:
            await self._check_service(conn, expected_service_id)
            if request_key is not None:
                previous = await self._existing_create(conn, request_key, request_hash)
                if previous is not None:
                    return {**previous, "_create_replayed": True}
            await conn.execute(sql, params)
            if request_key is not None:
                await conn.execute("INSERT INTO schedule_create_keys VALUES(?,?,?,?)",
                                   (request_key, request_hash, row_id, now_iso()))
            cursor = await conn.execute("SELECT * FROM scheduled_jobs WHERE id=?", (row_id,))
            row = await cursor.fetchone()
        return {**_hydrate(row), "_create_replayed": False} if request_key is not None else _hydrate(row)

    @staticmethod
    async def _check_service(conn, expected_service_id):
        if expected_service_id is not None:
            cursor = await conn.execute("SELECT service_id FROM artifact_service WHERE singleton=1")
            service = await cursor.fetchone()
            if service is None or service[0] != expected_service_id:
                raise ValueError("Service identity changed; review the schedule again")

    @staticmethod
    async def _existing_create(conn, request_key, request_hash):
        cursor = await conn.execute("SELECT request_hash,job_id FROM schedule_create_keys WHERE request_key=?", (request_key,))
        previous = await cursor.fetchone()
        if previous is None:
            return None
        if previous["request_hash"] != request_hash:
            raise ValueError("idempotency_key already belongs to a different schedule request")
        cursor = await conn.execute("SELECT * FROM scheduled_jobs WHERE id=?", (previous["job_id"],))
        row = await cursor.fetchone()
        if row is None:
            raise ValueError("Original schedule is unavailable; do not create a replacement automatically")
        return _hydrate(row)

    async def find_create(self, request_key, request_hash, expected_service_id):
        async with self._db.transaction() as conn:
            await self._check_service(conn, expected_service_id)
            return await self._existing_create(conn, request_key, request_hash)

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
                "UPDATE scheduled_jobs SET enabled=0,deleted_at=? WHERE id=? AND deleted_at IS NULL", (now_iso(), job_id)
            )
            await conn.execute("UPDATE schedule_occurrences SET status='cancelled',reason='Schedule deleted before admission',updated_at=?,completed_at=? WHERE job_id=? AND run_id IS NULL AND status IN ('pending','blocked')", (now_iso(),now_iso(),job_id))
            await conn.execute("UPDATE tasks SET status='cancelled',completed_at=? WHERE id IN (SELECT task_id FROM schedule_occurrences WHERE job_id=? AND run_id IS NULL AND status='cancelled')",(now_iso(),job_id))
            await conn.execute("UPDATE scheduled_jobs SET active_occurrence_id=NULL WHERE id=? AND active_occurrence_id IN (SELECT id FROM schedule_occurrences WHERE job_id=? AND run_id IS NULL)",(job_id,job_id))
            return cur.rowcount > 0

    async def count(self, *, enabled: bool | None = None) -> int:
        where, params = _filters(enabled=enabled)
        row = await self._db.fetchone(
            f"SELECT COUNT(*) AS n FROM scheduled_jobs {where}", tuple(params)
        )
        return int(row["n"]) if row else 0

    # -----------------------------------------------------------------------
    # Task-spec method names (thin aliases — keep the public surface stable
    # even if internal names differ). The scheduler task asks for these
    # exact names; we don't break the older names above because other code
    # (storage tests, internal queries) already uses them.
    # -----------------------------------------------------------------------

    async def list_all(self) -> list[dict[str, Any]]:
        """Return every scheduled job, regardless of ``enabled`` flag.

        Equivalent to ``list(enabled=None)`` with a deterministic order
        so IPC consumers can render a stable list.
        """
        rows = await self.list(order_by="name ASC")
        return [row for row in rows if not row.get("deleted_at")]

    async def update_enabled(
        self, job_id: str, enabled: bool
    ) -> dict[str, Any] | None:
        """Flip the ``enabled`` flag on a job and return the new row."""
        return await self.set_enabled(job_id, enabled)

    async def enable(self, job_id: str) -> dict[str, Any] | None:
        return await self.set_enabled(job_id, True)

    async def disable(self, job_id: str) -> dict[str, Any] | None:
        return await self.set_enabled(job_id, False)

    async def update_last_run(
        self, job_id: str, ts: str
    ) -> dict[str, Any] | None:
        """Set ``last_run_at`` only — leaves ``next_run_at`` untouched."""
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE scheduled_jobs SET last_run_at = ? WHERE id = ?",
                (ts, job_id),
            )
        return await self.get(job_id)

    async def update_next_run(
        self, job_id: str, ts: str
    ) -> dict[str, Any] | None:
        """Set ``next_run_at`` only — leaves ``last_run_at`` untouched."""
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE scheduled_jobs SET next_run_at = ? WHERE id = ?",
                (ts, job_id),
            )
        return await self.get(job_id)


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if d is None:
        return None
    d["payload"] = loads_json(d.get("payload"))
    d["context"] = loads_json(d.get("context"))
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
