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

_VALID_RUN_MODES = frozenset({"chat", "plan", "execute", "team", "subagent"})
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
            run = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id = ?", (run_id,)))
            await _append_event(conn, run_id, "created", {"run": run})
        return run

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM agent_runs WHERE id = ?", (run_id,))
        return _hydrate_run(row)

    async def list_runs(
        self,
        *,
        session_id: str | None = None,
        status: str | None = None,
        mode: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        if mode is not None and mode not in _VALID_RUN_MODES:
            raise ValueError(f"mode must be one of {sorted(_VALID_RUN_MODES)}")
        where: list[str] = []
        params: list[Any] = []
        if session_id is not None:
            where.append("session_id = ?")
            params.append(session_id)
        if status is not None:
            where.append("status = ?")
            params.append(status)
        if mode is not None:
            where.append("mode = ?")
            params.append(mode)
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
            run = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id = ?", (run_id,)))
            if run is not None:
                await _append_event(conn, run_id, "status", {"run": run})
        return run

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
        async with self._db.transaction() as conn:
            owner = await _fetchone(conn, "SELECT session_id FROM agent_runs WHERE id = ?", (run_id,))
            if owner is None or owner["session_id"] != session_id:
                raise ValueError("step session must match its run")
            if ordinal is None:
                row = await _fetchone(conn,
                    "SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal "
                    "FROM agent_run_steps WHERE run_id = ?", (run_id,))
                ordinal = int(row["next_ordinal"])
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
            step = _hydrate_step(await _fetchone(conn, "SELECT * FROM agent_run_steps WHERE id = ?", (step_id,)))
            await _append_event(conn, run_id, "step_created", {"step": step})
        return step

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
            step = _hydrate_step(await _fetchone(conn, "SELECT * FROM agent_run_steps WHERE id = ?", (step_id,)))
            if step is not None:
                await _append_event(conn, step["run_id"], "step_updated", {"step": step})
        return step

    async def snapshot(self, run_id: str) -> dict[str, Any] | None:
        """Read state and its cursor in one transaction, including legacy runs."""
        async with self._db.transaction() as conn:
            run = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id = ?", (run_id,)))
            if run is None:
                return None
            async with conn.execute(
                "SELECT * FROM agent_run_steps WHERE run_id = ? ORDER BY ordinal, id", (run_id,)
            ) as cursor:
                steps = [_hydrate_step(row) for row in await cursor.fetchall()]
            row = await _fetchone(conn, "SELECT COALESCE(MAX(seq), 0) AS seq FROM run_events WHERE run_id = ?", (run_id,))
            return {"run": run, "steps": steps, "cursor": row["seq"]}

    async def events(self, run_id: str, *, after_seq: int = 0, limit: int = 100) -> dict[str, Any] | None:
        """Return bounded ordered changes strictly after a run-scoped cursor."""
        if type(after_seq) is not int or not 0 <= after_seq <= 2**53 - 1:
            raise ValueError("after_seq must be a non-negative safe integer")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer from 1 to 500")
        async with self._db.transaction() as conn:
            if await _fetchone(conn, "SELECT id FROM agent_runs WHERE id = ?", (run_id,)) is None:
                return None
            row = await _fetchone(conn, "SELECT COALESCE(MAX(seq), 0) AS seq FROM run_events WHERE run_id = ?", (run_id,))
            if after_seq > row["seq"]:
                raise ValueError("cursor is ahead of this run; reload its snapshot")
            async with conn.execute(
                "SELECT * FROM run_events WHERE run_id = ? AND seq > ? ORDER BY seq LIMIT ?",
                (run_id, after_seq, limit + 1),
            ) as cursor:
                rows = await cursor.fetchall()
        events = []
        for row in rows[:limit]:
            event = dict(row)
            event["data"] = loads_json(event["data"])
            events.append(event)
        return {"events": events, "next_seq": events[-1]["seq"] if events else after_seq,
                "has_more": len(rows) > limit}

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

    async def recover_orphans(
        self,
        *,
        reason: str = "orphaned by unexpected shutdown",
    ) -> dict[str, Any]:
        """Atomically close orphan runs and record recovery; never repeat tools.

        Keeps the legacy ``failed`` status until the RunService state migration.
        Only called at boot, before accepting work.
        """
        ts = now_iso()
        async with self._db.transaction() as conn:
            async with conn.execute(
                "SELECT id, session_id FROM agent_runs "
                "WHERE status IN ('planning', 'running', 'awaiting_approval')"
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                run_id, session_id = row["id"], row["session_id"]
                await conn.execute(
                    "UPDATE agent_runs SET status = 'failed', error = ?, "
                    "completed_at = COALESCE(completed_at, ?) WHERE id = ?",
                    (reason, ts, run_id),
                )
                # An unfinished tool may already have changed files. Keep its
                # result unknown and visible; closing a spinner is not retrying it.
                async with conn.execute(
                    "SELECT id FROM agent_run_steps WHERE run_id = ? "
                    "AND status IN ('pending', 'running')", (run_id,)
                ) as cursor:
                    unfinished = await cursor.fetchall()
                for pending in unfinished:
                    await conn.execute(
                        "UPDATE agent_run_steps SET status = 'failed', error = ?, "
                        "completed_at = ? WHERE id = ?", (reason, ts, pending["id"]),
                    )
                    step = _hydrate_step(await _fetchone(conn,
                        "SELECT * FROM agent_run_steps WHERE id = ?", (pending["id"],)))
                    await _append_event(conn, run_id, "step_updated", {"step": step})
                step_id = f"step_{uuid.uuid4().hex[:12]}"
                await conn.execute(
                    "INSERT INTO agent_run_steps "
                    "(id, run_id, session_id, kind, status, title, summary, payload, "
                    "started_at, completed_at, ordinal) "
                    "SELECT ?, ?, ?, 'status', 'completed', ?, ?, ?, ?, ?, "
                    "COALESCE(MAX(ordinal), 0) + 1 FROM agent_run_steps WHERE run_id = ?",
                    (step_id, run_id, session_id, "recovered after unexpected shutdown", reason,
                     dumps_json({"recovered_at": ts, "recovery_reason": reason,
                                 "schema_version": 1, "requires_review": True}), ts, ts, run_id),
                )
                step = _hydrate_step(await _fetchone(conn,
                    "SELECT * FROM agent_run_steps WHERE id = ?", (step_id,)))
                run = _hydrate_run(await _fetchone(conn,
                    "SELECT * FROM agent_runs WHERE id = ?", (run_id,)))
                await _append_event(conn, run_id, "recovered", {"run": run, "step": step})
        run_ids = [row["id"] for row in rows]
        sessions = [row["session_id"] for row in rows]
        return {"recovered": len(run_ids), "run_ids": run_ids, "sessions": sessions}


async def _fetchone(conn: Any, sql: str, params: tuple = ()) -> Any:
    async with conn.execute(sql, params) as cursor:
        return await cursor.fetchone()


async def _append_event(conn: Any, run_id: str, kind: str, data: dict[str, Any]) -> None:
    """Caller owns the write transaction; event failure rolls back state too."""
    await conn.execute(
        "INSERT INTO run_events (id, run_id, seq, type, created_at, data) "
        "SELECT ?, ?, COALESCE(MAX(seq), 0) + 1, ?, ?, ? FROM run_events WHERE run_id = ?",
        (f"evt_{uuid.uuid4().hex}", run_id, kind, now_iso(), dumps_json(data), run_id),
    )


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
