"""DAO — workflows.

CRUD operations for the ``workflows`` table. Supports enable/disable
toggle and run counter increment.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from ._base import (
    apply_pagination,
    now_iso,
    row_to_dict,
)

logger = logging.getLogger(__name__)


class WorkflowDAO:
    """Async DAO for the ``workflows`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        description: str = "",
        trigger_type: str,
        trigger_config: dict[str, Any] | None = None,
        steps: list[dict[str, Any]] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        row_id = f"wf_{uuid.uuid4().hex[:10]}"
        now = now_iso()
        tc_json = json.dumps(trigger_config or {}, ensure_ascii=False)
        steps_json = json.dumps(steps or [], ensure_ascii=False)
        sql = (
            "INSERT INTO workflows "
            "(id, name, description, enabled, trigger_type, trigger_config, "
            "steps, last_run_at, run_count, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 0, ?, ?)"
        )
        params = (
            row_id, name, description, int(enabled), trigger_type,
            tc_json, steps_json, now, now,
        )
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone(
            "SELECT * FROM workflows WHERE id = ?", (row_id,)
        )
        return _hydrate(row)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, workflow_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM workflows WHERE id = ?", (workflow_id,)
        )
        return _hydrate(row) if row else None

    async def list_all(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        trigger_type: str | None = None,
        enabled_only: bool = False,
    ) -> tuple[list[dict[str, Any]], int]:
        where, params = _filters(trigger_type=trigger_type, enabled_only=enabled_only)
        count_sql = f"SELECT COUNT(*) AS n FROM workflows {where}"
        row = await self._db.fetchone(count_sql, tuple(params))
        total = int(row["n"]) if row else 0

        list_sql = f"SELECT * FROM workflows {where} ORDER BY created_at DESC"
        list_sql, params = apply_pagination(list_sql, list(params), limit=limit, offset=offset)
        rows = await self._db.fetchall(list_sql, tuple(params))
        return [_hydrate(r) for r in rows], total

    async def list_by_trigger(self, trigger_type: str, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        where, params = _filters(trigger_type=trigger_type, enabled_only=enabled_only)
        sql = f"SELECT * FROM workflows {where} ORDER BY created_at"
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    async def update(
        self,
        workflow_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        trigger_config: dict[str, Any] | None = None,
        steps: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        sets: list[str] = []
        vals: list[Any] = []
        if name is not None:
            sets.append("name = ?")
            vals.append(name)
        if description is not None:
            sets.append("description = ?")
            vals.append(description)
        if trigger_config is not None:
            sets.append("trigger_config = ?")
            vals.append(json.dumps(trigger_config, ensure_ascii=False))
        if steps is not None:
            sets.append("steps = ?")
            vals.append(json.dumps(steps, ensure_ascii=False))
        if not sets:
            return await self.get(workflow_id)
        sets.append("updated_at = ?")
        vals.append(now_iso())
        vals.append(workflow_id)
        async with self._db.transaction() as conn:
            await conn.execute(
                f"UPDATE workflows SET {', '.join(sets)} WHERE id = ?",
                tuple(vals),
            )
        return await self.get(workflow_id)

    async def enable(self, workflow_id: str) -> dict[str, Any] | None:
        now = now_iso()
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE workflows SET enabled = 1, updated_at = ? WHERE id = ?",
                (now, workflow_id),
            )
        return await self.get(workflow_id)

    async def disable(self, workflow_id: str) -> dict[str, Any] | None:
        now = now_iso()
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE workflows SET enabled = 0, updated_at = ? WHERE id = ?",
                (now, workflow_id),
            )
        return await self.get(workflow_id)

    async def increment_run(self, workflow_id: str) -> None:
        now = now_iso()
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE workflows SET run_count = run_count + 1, last_run_at = ? WHERE id = ?",
                (now, workflow_id),
            )

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete(self, workflow_id: str) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM workflows WHERE id = ?", (workflow_id,)
            )
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any]:
    d = row_to_dict(row)
    if d is None:
        return {"id": "unknown"}
    if "enabled" in d:
        d["enabled"] = bool(d["enabled"])
    if "trigger_config" in d and isinstance(d["trigger_config"], str):
        try:
            d["trigger_config"] = json.loads(d["trigger_config"])
        except (json.JSONDecodeError, TypeError):
            d["trigger_config"] = {}
    if "steps" in d and isinstance(d["steps"], str):
        try:
            d["steps"] = json.loads(d["steps"])
        except (json.JSONDecodeError, TypeError):
            d["steps"] = []
    return d


def _filters(
    *,
    trigger_type: str | None = None,
    enabled_only: bool = False,
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if trigger_type is not None:
        clauses.append("trigger_type = ?")
        params.append(trigger_type)
    if enabled_only:
        clauses.append("enabled = 1")
    if not clauses:
        return "", []
    return "WHERE " + " AND ".join(clauses), params


__all__ = ["WorkflowDAO"]
