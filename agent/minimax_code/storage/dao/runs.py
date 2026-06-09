"""DAO — agent run timeline storage."""

from __future__ import annotations

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

_VALID_RUN_MODES = frozenset({"chat", "plan", "execute"})
_VALID_RUN_STATUS = frozenset(
    {"planning", "running", "awaiting_approval", "completed", "failed", "cancelled"}
)
_VALID_STEP_KIND = frozenset(
    {"thought", "status", "plan", "tool_call", "observation", "approval", "patch", "final"}
)
_VALID_STEP_STATUS = frozenset({"pending", "running", "completed", "failed", "cancelled"})
_RUN_SORTABLE = ("created_at", "completed_at", "status", "id")
_STEP_SORTABLE = ("ordinal", "started_at", "completed_at", "id")


class AgentRunsDAO:
    """Async DAO for ``agent_runs`` and ``agent_run_steps``."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def create_run(
        self,
        *,
        session_id: str,
        id: str | None = None,
        mode: str = "chat",
        status: str = "running",
        title: str = "",
        user_message_id: str | None = None,
        assistant_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if mode not in _VALID_RUN_MODES:
            raise ValueError(f"mode must be one of {sorted(_VALID_RUN_MODES)}")
        if status not in _VALID_RUN_STATUS:
            raise ValueError(f"status must be one of {sorted(_VALID_RUN_STATUS)}")
        run_id = id or f"run_{uuid.uuid4().hex[:12]}"
        ts = now_iso()
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT INTO agent_runs "
                "(id, session_id, mode, status, title, user_message_id, "
                " assistant_message_id, created_at, started_at, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    session_id,
                    mode,
                    status,
                    title,
                    user_message_id,
                    assistant_message_id,
                    ts,
                    ts,
                    dumps_json(metadata),
                ),
            )
        row = await self._db.fetchone("SELECT * FROM agent_runs WHERE id = ?", (run_id,))
        return _hydrate_run(row)

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM agent_runs WHERE id = ?", (run_id,))
        return _hydrate_run(row)

    async def list_runs(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            where.append("session_id = ?")
            params.append(session_id)
        if status is not None:
            where.append("status = ?")
            params.append(status)
        where_sql = "WHERE " + " AND ".join(where) if where else ""
        sql = (
            f"SELECT * FROM agent_runs {where_sql} "
            f"ORDER BY {parse_order_by(order_by, _RUN_SORTABLE)}"
        )
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate_run(r) for r in rows]

    async def update_run_status(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None = None,
        assistant_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if status not in _VALID_RUN_STATUS:
            raise ValueError(f"status must be one of {sorted(_VALID_RUN_STATUS)}")
        sets = ["status = ?"]
        params: list[Any] = [status]
        if status in {"completed", "failed", "cancelled"}:
            sets.append("completed_at = COALESCE(completed_at, ?)")
            params.append(now_iso())
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        if assistant_message_id is not None:
            sets.append("assistant_message_id = ?")
            params.append(assistant_message_id)
        if metadata is not None:
            sets.append("metadata = ?")
            params.append(dumps_json(metadata))
        params.append(run_id)
        async with self._db.transaction() as conn:
            await conn.execute(
                f"UPDATE agent_runs SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
        return await self.get_run(run_id)

    async def create_step(
        self,
        *,
        run_id: str,
        session_id: str,
        kind: str,
        id: str | None = None,
        status: str = "running",
        title: str = "",
        summary: str = "",
        tool_call_id: str | None = None,
        tool_name: str | None = None,
        parent_id: str | None = None,
        payload: dict[str, Any] | None = None,
        ordinal: int | None = None,
    ) -> dict[str, Any]:
        if kind not in _VALID_STEP_KIND:
            raise ValueError(f"kind must be one of {sorted(_VALID_STEP_KIND)}")
        if status not in _VALID_STEP_STATUS:
            raise ValueError(f"status must be one of {sorted(_VALID_STEP_STATUS)}")
        step_id = id or f"step_{uuid.uuid4().hex[:12]}"
        if ordinal is None:
            row = await self._db.fetchone(
                "SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal "
                "FROM agent_run_steps WHERE run_id = ?",
                (run_id,),
            )
            ordinal = int(row["next_ordinal"]) if row else 1
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT INTO agent_run_steps "
                "(id, run_id, session_id, kind, status, title, summary, "
                " tool_call_id, tool_name, parent_id, payload, started_at, ordinal) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    step_id,
                    run_id,
                    session_id,
                    kind,
                    status,
                    title,
                    summary,
                    tool_call_id,
                    tool_name,
                    parent_id,
                    dumps_json(payload),
                    now_iso(),
                    ordinal,
                ),
            )
        row = await self._db.fetchone("SELECT * FROM agent_run_steps WHERE id = ?", (step_id,))
        return _hydrate_step(row)

    async def complete_step(
        self,
        step_id: str,
        *,
        status: str = "completed",
        summary: str | None = None,
        payload: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        if status not in _VALID_STEP_STATUS:
            raise ValueError(f"status must be one of {sorted(_VALID_STEP_STATUS)}")
        row = await self._db.fetchone(
            "SELECT started_at FROM agent_run_steps WHERE id = ?",
            (step_id,),
        )
        completed_at = now_iso()
        sets = ["status = ?", "completed_at = ?"]
        params: list[Any] = [status, completed_at]
        if row and row["started_at"]:
            duration_ms = _duration_ms(str(row["started_at"]), completed_at)
            if duration_ms is not None:
                sets.append("duration_ms = ?")
                params.append(duration_ms)
        if summary is not None:
            sets.append("summary = ?")
            params.append(summary)
        if payload is not None:
            sets.append("payload = ?")
            params.append(dumps_json(payload))
        if error is not None:
            sets.append("error = ?")
            params.append(error)
        params.append(step_id)
        async with self._db.transaction() as conn:
            await conn.execute(
                f"UPDATE agent_run_steps SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
        return await self.get_step(step_id)

    async def get_step(self, step_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM agent_run_steps WHERE id = ?",
            (step_id,),
        )
        return _hydrate_step(row)

    async def list_steps(
        self,
        run_id: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM agent_run_steps WHERE run_id = ? "
            f"ORDER BY {parse_order_by(order_by, _STEP_SORTABLE, default='ordinal ASC')}"
        )
        params: list[Any] = [run_id]
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate_step(r) for r in rows]


def _hydrate_run(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if d is None:
        return None
    d["metadata"] = loads_json(d.get("metadata"))
    return d


def _hydrate_step(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if d is None:
        return None
    d["payload"] = loads_json(d.get("payload"))
    return d


def _duration_ms(started_at: str, completed_at: str) -> int | None:
    try:
        from datetime import datetime

        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        return max(0, int((end - start).total_seconds() * 1000))
    except Exception:
        return None


__all__ = ["AgentRunsDAO"]
