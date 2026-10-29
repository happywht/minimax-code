"""DAO — agent run timeline storage."""

from __future__ import annotations

import hashlib
import json
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
    {"queued", "planning", "running", "awaiting_approval", "waiting_input",
     "budget_paused", "interrupted", "completed", "failed", "cancelled"}
)
_VALID_STEP_KIND = frozenset(
    {"thought", "status", "plan", "tool_call", "observation", "approval", "patch", "final"}
)
_VALID_STEP_STATUS = frozenset({"pending", "running", "completed", "failed", "cancelled"})
_RUN_SORTABLE = ("created_at", "completed_at", "status", "id")
_STEP_SORTABLE = ("ordinal", "started_at", "completed_at", "id")


class RequestConflictError(ValueError):
    """The same idempotency key cannot describe different user input."""


def _request_hash(request: dict[str, Any]) -> str:
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class AgentRunsDAO:
    """Async DAO for ``agent_runs`` and ``agent_run_steps``."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def find_request(self, session_id: str, key: str, request: dict[str, Any]):
        row = await self._db.fetchone(
            "SELECT * FROM agent_runs WHERE session_id = ? AND request_key = ?", (session_id, key))
        if row is not None and row["request_hash"] != _request_hash(request):
            raise RequestConflictError("idempotency_key already belongs to different input")
        return _hydrate_run(row)

    async def find_agent_root(self, source_session_id, key, intent):
        row = await self._db.fetchone(
            "SELECT a.request_hash,r.* FROM agent_root_admissions a JOIN agent_runs r ON r.id=a.run_id "
            "WHERE a.source_session_id=? AND a.request_key=?", (source_session_id, key))
        if row is not None and row["request_hash"] != _request_hash(intent):
            raise RequestConflictError("idempotency_key already belongs to different agent input")
        return _hydrate_run(row)

    async def admit_run(
        self, *, session_id: str, request: dict[str, Any], title: str,
        metadata: dict[str, Any], request_key: str | None = None,
        resumed_from_id: str | None = None,
        continuation: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        """Commit a queued request and its first event before any execution.

        Retries compare user intent, not mutable global defaults. The first
        request's saved execution context remains authoritative.
        """
        if request_key is not None and (
            not isinstance(request_key, str) or not request_key.strip() or len(request_key) > 128
        ):
            raise ValueError("idempotency_key must be a non-empty string of at most 128 characters")
        mode = request.get("mode", "chat")
        if mode not in {"chat", "plan", "execute"} and not (mode == "team" and request.get("kind") == "team_spawn"):
            raise ValueError("native admission requires chat, plan or execute mode")
        canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        fingerprint = _request_hash(request)
        async with self._db.transaction() as conn:
            agent_profile = metadata.get("agent_execution")
            root_admission = agent_profile is not None and not resumed_from_id
            if root_admission:
                source_id = agent_profile["source_session_id"]
                intent_hash = _request_hash(agent_profile["intent"])
                existing = await _fetchone(conn,
                    "SELECT * FROM agent_root_admissions WHERE source_session_id=? AND request_key=?",
                    (source_id, request_key))
                if existing is not None:
                    if existing["request_hash"] != intent_hash:
                        raise RequestConflictError("idempotency_key already belongs to different agent input")
                    return _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id=?", (existing["run_id"],))), False
                source = await _fetchone(conn, "SELECT * FROM sessions WHERE id=?", (source_id,))
                if source is None or source["engine"] != "native":
                    raise ValueError("Agent root requires an existing explicit Native source session")
                if source["project_id"] != agent_profile["project_id"]:
                    raise ValueError("Agent source project changed during admission; retry")
                from pathlib import Path
                binding = source["workspace_path"]
                if not binding and source["project_id"]:
                    project = await _fetchone(conn, "SELECT root_path FROM projects WHERE id=?", (source["project_id"],))
                    binding = project["root_path"] if project else None
                if binding and str(Path(binding).resolve(strict=True)) != agent_profile["source_workspace"]:
                    raise ValueError("Agent source workspace changed during admission; retry")
                ts = now_iso()
                await conn.execute(
                    "INSERT INTO sessions(id,title,created_at,updated_at,model,system_prompt,workspace_path,project_id,engine) "
                    "VALUES(?,?,?,?,?,?,?,?, 'native')",
                    (session_id, "agent:" + agent_profile["agent"], ts, ts,
                     agent_profile["model_snapshot"]["model"], "", agent_profile["workspace"], source["project_id"]))
            if request_key is not None:
                old = await _fetchone(conn, "SELECT * FROM agent_runs WHERE session_id = ? AND request_key = ?",
                                      (session_id, request_key))
                if old is not None:
                    if old["request_hash"] != fingerprint:
                        raise RequestConflictError("idempotency_key already belongs to different input")
                    return _hydrate_run(old), False
            pending = await _fetchone(conn,
                "SELECT COUNT(*) AS n FROM agent_runs WHERE session_id = ? AND request IS NOT NULL "
                "AND status IN ('queued', 'planning', 'running', 'awaiting_approval', 'waiting_input', 'budget_paused')",
                (session_id,))
            if pending["n"] >= 32:
                raise ValueError("Session queue is full (32 pending tasks)")
            if continuation:
                source = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id = ?", (resumed_from_id,)))
                if (source is None or source["session_id"] != session_id
                        or source["status"] not in {"completed", "cancelled"}
                        or not (source.get("metadata") or {}).get("truncated")):
                    raise ValueError("Continuation requires a truncated run in the same session")
                source["metadata"] = {**source["metadata"], "continued": True}
                await conn.execute("UPDATE agent_runs SET metadata = ? WHERE id = ?",
                                   (dumps_json(source["metadata"]), resumed_from_id))
                await _append_event(conn, resumed_from_id, "status", {"run": source})
            run_id, ts = f"run_{uuid.uuid4().hex}", now_iso()
            codex = request.get("engine") == "codex"
            if codex:
                session = await _fetchone(conn, "SELECT * FROM sessions WHERE id=?", (session_id,))
                if session is None or session["engine"] != "codex":
                    raise ValueError("Codex admission requires a pinned Codex session")
                state = loads_json(session["engine_state"]) or {}
                if state.get("handoff_required"):
                    raise ValueError("Legacy engine history is ambiguous; create a new session with a handoff")
                unresolved = await _fetchall(conn,
                    "SELECT * FROM agent_runs WHERE session_id=? AND started_at IS NOT NULL "
                    "AND status IN ('failed','interrupted','cancelled','budget_paused')", (session_id,))
                for prior in unresolved:
                    md = loads_json(prior["metadata"]) or {}
                    if md.get("engine") == "codex" and not md.get("recovery_child_id"):
                        if prior["id"] != resumed_from_id or not metadata.get("reviewed"):
                            raise ValueError("Review the unresolved Codex run and use run.resume before sending new work")
                if resumed_from_id:
                    source = await _fetchone(conn, "SELECT * FROM agent_runs WHERE id=?", (resumed_from_id,))
                    source_md = loads_json(source["metadata"]) if source else {}
                    if (not source or source["session_id"] != session_id
                            or source_md.get("engine") != "codex"
                            or source_md.get("recovery_child_id")
                            or source["status"] not in {"failed", "cancelled", "interrupted", "budget_paused"}):
                        raise ValueError("Invalid or already resumed Codex recovery source")
                    source_md = {**source_md, "recovery_child_id": run_id}
                    await conn.execute("UPDATE agent_runs SET metadata=? WHERE id=?",
                        (dumps_json(source_md), resumed_from_id))
                    source_run = _hydrate_run(source)
                    source_run["metadata"] = source_md
                    await _append_event(conn, resumed_from_id, "status", {"run": source_run})
            await conn.execute(
                "INSERT INTO agent_runs (id, session_id, mode, status, title, created_at, "
                "assistant_message_id, metadata, request_key, request_hash, request, resumed_from_id) "
                "VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_id, session_id, mode, title, ts, f"msg_{uuid.uuid4().hex}",
                 dumps_json(metadata), request_key, fingerprint, canonical, resumed_from_id))
            if root_admission:
                await conn.execute(
                    "INSERT INTO agent_root_admissions VALUES(?,?,?,?,?,?)",
                    (source_id, request_key, intent_hash, session_id, run_id, ts))
            from .budgets import attach_family
            await attach_family(conn, run_id, metadata, source_id=resumed_from_id)
            run = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id = ?", (run_id,)))
            if metadata.get("schedule"):
                binding = metadata["schedule"]
                occurrence = await _fetchone(conn, "SELECT o.*,j.active_occurrence_id,j.enabled AS job_enabled,j.deleted_at,j.revision AS job_revision,j.binding_state FROM schedule_occurrences o JOIN scheduled_jobs j ON j.id=o.job_id WHERE o.id=?", (binding["occurrence_id"],))
                if (not occurrence or occurrence["session_id"] != session_id
                        or occurrence["active_occurrence_id"] != occurrence["id"]
                        or (occurrence["run_id"] and occurrence["run_id"] != resumed_from_id)):
                    raise ValueError("Scheduled occurrence ownership changed")
                if not resumed_from_id and (not occurrence["job_enabled"] or occurrence["deleted_at"]
                        or occurrence["job_revision"] != occurrence["revision"] or occurrence["binding_state"] != "ready"):
                    raise ValueError("Schedule changed before admission; dispatch is suspended")
                await conn.execute("UPDATE schedule_occurrences SET run_id=?,status='running',updated_at=? WHERE id=?", (run_id, ts, occurrence["id"]))
                await conn.execute("UPDATE tasks SET run_id=? WHERE id=?", (run_id, occurrence["task_id"]))
            if codex:
                user_id = f"msg_{uuid.uuid4().hex}"
                await conn.execute(
                    "INSERT INTO messages(id,session_id,role,content,created_at,metadata) VALUES(?,?,'user',?,?,?)",
                    (user_id, session_id, request["content"], ts,
                     dumps_json({"run_id": run_id, "engine": "codex", "request_key": request_key})))
                await link_run_message(conn, run_id, session_id, user_id, "user")
                run["user_message_id"] = user_id
            await _append_event(conn, run_id, "created", {"run": run})
            return run, True

    async def claim_run(self, run_id: str) -> dict[str, Any] | None:
        """A committed queued record can be claimed once; no implicit retry."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE agent_runs SET status = 'running', started_at = ? WHERE id = ? AND status = 'queued' "
                "AND COALESCE(json_extract(metadata, '$.cancel_requested'), 0) != 1",
                (now_iso(), run_id))
            if cursor.rowcount != 1:
                return None
            run = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id = ?", (run_id,)))
            await _append_event(conn, run_id, "status", {"run": run})
            return run

    async def request_queued_cancel(self, run_id: str) -> dict[str, Any] | None:
        """Persist a cancellation fence without ending the session lease."""
        async with self._db.transaction() as conn:
            row = await _fetchone(conn, "SELECT * FROM agent_runs WHERE id = ?", (run_id,))
            if row is None or row["status"] != "queued":
                return None
            metadata = loads_json(row["metadata"]) or {}
            if metadata.get("cancel_requested") is not True:
                metadata["cancel_requested"] = True
                await conn.execute("UPDATE agent_runs SET metadata = ? WHERE id = ?",
                                   (dumps_json(metadata), run_id))
                run = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id = ?", (run_id,)))
                await _append_event(conn, run_id, "status", {"run": run})
                return run
            return _hydrate_run(row)

    async def request_payload(self, run_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT request FROM agent_runs WHERE id = ?", (run_id,))
        return loads_json(row["request"]) if row else None

    async def result_payload(self, run_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT result FROM agent_runs WHERE id = ?", (run_id,))
        return loads_json(row["result"]) if row else None

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
        attach_budget_family: bool = False,
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
            if attach_budget_family:
                from .budgets import attach_family
                metadata = dict(metadata or {})
                await attach_family(conn, run_id, metadata)
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
        ordering = parse_order_by(order_by, _RUN_SORTABLE)
        direction = "DESC" if ordering.endswith("DESC") else "ASC"
        sql = (
            f"SELECT * FROM agent_runs {where_sql} "
            f"ORDER BY {ordering}, rowid {direction}"
        )
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate_run(r) for r in rows]

    async def list_recovery_runs(self, session_id: str, *, limit: int = 20) -> dict[str, Any]:
        """Read recent and active runs from one SQLite snapshot, with a bounded active set."""
        if not session_id or not 1 <= limit <= 100:
            raise ValueError("invalid recovery session or limit")
        rows = await self._db.fetchall(
            """WITH recent AS (
                SELECT rowid AS rid FROM agent_runs WHERE session_id=?
                ORDER BY created_at DESC, rowid DESC LIMIT ?
            ), active AS (
                SELECT rowid AS rid FROM agent_runs WHERE session_id=? AND status IN
                    ('queued','planning','running','awaiting_approval','waiting_input')
                ORDER BY created_at DESC, rowid DESC LIMIT 101
            )
            SELECT *, (SELECT COUNT(*) FROM active) AS _active_count FROM agent_runs
            WHERE rowid IN (SELECT rid FROM recent UNION SELECT rid FROM active)
            ORDER BY created_at DESC, rowid DESC""",
            (session_id, limit, session_id),
        )
        truncated = bool(rows and rows[0]["_active_count"] > 100)
        runs = []
        for row in rows:
            data = dict(row)
            data.pop("_active_count", None)
            runs.append(_hydrate_run(data))
        return {"runs": runs, "active_truncated": truncated}

    async def update_run_status(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None = None,
        assistant_message_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        if status not in _VALID_RUN_STATUS:
            raise ValueError(f"status must be one of {sorted(_VALID_RUN_STATUS)}")
        sets = ["status = ?"]
        params: list[Any] = [status]
        if status in {"completed", "failed", "cancelled", "interrupted"}:
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
        if result is not None:
            sets.append("result = ?")
            params.append(dumps_json(result))
        params.append(run_id)
        async with self._db.transaction() as conn:
            await conn.execute(
                f"UPDATE agent_runs SET {', '.join(sets)} WHERE id = ?",
                tuple(params),
            )
            run = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id = ?", (run_id,)))
            if run is not None:
                verification = (metadata or {}).get("verification") or (result or {}).get("verification")
                if verification:
                    await _save_message_verification(conn, run, verification)
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

    async def checkpoint_running_step(
        self, step_id: str, *, payload: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Persist evidence only while both the step and its run remain active."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE agent_run_steps SET payload = ? WHERE id = ? AND status = 'running' "
                "AND EXISTS (SELECT 1 FROM agent_runs WHERE id = agent_run_steps.run_id "
                "AND status = 'running')",
                (dumps_json(payload), step_id),
            )
            if not cursor.rowcount:
                return None
            step = _hydrate_step(await _fetchone(
                conn, "SELECT * FROM agent_run_steps WHERE id = ?", (step_id,)
            ))
            await _append_event(conn, step["run_id"], "step_updated", {"step": step})
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

    async def record_tool_result(
        self, *, run_id: str, session_id: str, parent_id: str | None,
        tool_call_id: str | None, tool_name: str, success: bool,
        summary: str, payload: dict[str, Any], error: str | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        """Commit the tool outcome and its observation as one journal transition."""
        ts = now_iso()
        status = "completed" if success else "failed"
        observation_id = f"step_{uuid.uuid4().hex[:12]}"
        async with self._db.transaction() as conn:
            owner = await _fetchone(conn, "SELECT session_id FROM agent_runs WHERE id = ?", (run_id,))
            if owner is None or owner["session_id"] != session_id:
                raise ValueError("step session must match its run")
            parent = None
            if parent_id:
                row = await _fetchone(conn,
                    "SELECT * FROM agent_run_steps WHERE id = ? AND run_id = ? AND kind = 'tool_call'",
                    (parent_id, run_id))
                if row is None or row["status"] != "running":
                    raise ValueError("tool call is not running")
                duration = _duration_ms(str(row["started_at"]), ts) if row["started_at"] else None
                await conn.execute(
                    "UPDATE agent_run_steps SET status=?, summary=?, error=?, completed_at=?, duration_ms=? "
                    "WHERE id=?", (status, "Completed" if success else str(error or "Tool failed"),
                                   error, ts, duration, parent_id))
                parent = _hydrate_step(await _fetchone(conn,
                    "SELECT * FROM agent_run_steps WHERE id=?", (parent_id,)))
                await _append_event(conn, run_id, "step_updated", {"step": parent})
            row = await _fetchone(conn,
                "SELECT COALESCE(MAX(ordinal), 0) + 1 AS ordinal FROM agent_run_steps WHERE run_id=?",
                (run_id,))
            await conn.execute(
                "INSERT INTO agent_run_steps (id,run_id,session_id,kind,status,title,summary,"
                "tool_call_id,tool_name,parent_id,payload,started_at,ordinal) "
                "VALUES (?,?,?,'observation','running',?,?,?,?,?,?,?,?)",
                (observation_id, run_id, session_id, f"{tool_name} result", summary,
                 tool_call_id, tool_name, parent_id, dumps_json(payload), ts, row["ordinal"]))
            observation = _hydrate_step(await _fetchone(conn,
                "SELECT * FROM agent_run_steps WHERE id=?", (observation_id,)))
            await _append_event(conn, run_id, "step_created", {"step": observation})
            await conn.execute(
                "UPDATE agent_run_steps SET status=?, error=?, completed_at=?, duration_ms=0 WHERE id=?",
                (status, error, ts, observation_id))
            observation = _hydrate_step(await _fetchone(conn,
                "SELECT * FROM agent_run_steps WHERE id=?", (observation_id,)))
            await _append_event(conn, run_id, "step_updated", {"step": observation})
        return parent, observation

    async def snapshot(self, run_id: str) -> dict[str, Any] | None:
        """Read state and its cursor in one transaction, including legacy runs."""
        async with self._db.transaction() as conn:
            run = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id = ?", (run_id,)))
            if run is None:
                return None
            async with conn.execute(
                "SELECT * FROM agent_run_steps WHERE run_id = ? AND COALESCE(tool_name,'') != '__captured_output_v1__' ORDER BY ordinal, id", (run_id,)
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
            "SELECT * FROM agent_run_steps WHERE id = ? AND COALESCE(tool_name,'') != '__captured_output_v1__'",
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
            "SELECT * FROM agent_run_steps WHERE run_id = ? AND COALESCE(tool_name,'') != '__captured_output_v1__' "
            f"ORDER BY {parse_order_by(order_by, _STEP_SORTABLE, default='ordinal ASC')}"
        )
        params: list[Any] = [run_id]
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate_step(r) for r in rows]

    async def terminate_run(
        self, run_id: str, *, status: str, error: str, result: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Close active steps and the run atomically, after execution has stopped."""
        if status not in {"failed", "cancelled"}:
            raise ValueError("termination status must be failed or cancelled")
        ts = now_iso()
        async with self._db.transaction() as conn:
            row = await _fetchone(conn, "SELECT * FROM agent_runs WHERE id = ?", (run_id,))
            if row is None or row["status"] in {"completed", "failed", "cancelled", "interrupted"}:
                return _hydrate_run(row)
            profile = (loads_json(row["metadata"]) or {}).get("agent_execution")
            if profile is not None:
                from ...agent_execution import terminal_receipt
                result = terminal_receipt(profile, _hydrate_run(row), result, status)
            async with conn.execute(
                "SELECT id, kind, payload FROM agent_run_steps WHERE run_id = ? AND status IN ('pending', 'running')",
                (run_id,),
            ) as cursor:
                steps = await cursor.fetchall()
            for step in steps:
                payload = step["payload"]
                step_error = error
                if step["kind"] == "tool_call":
                    original = _load_step_payload(payload)
                    details = original.copy() if isinstance(original, dict) else (
                        {} if original is None else {"original_payload": original})
                    payload = dumps_json({**details, "outcome": "unknown", "requires_review": True})
                    step_error = f"{error}; tool outcome unknown, review side effects"
                await conn.execute(
                    "UPDATE agent_run_steps SET status = ?, error = ?, completed_at = ?, payload = ? WHERE id = ?",
                    (status, step_error, ts, payload, step["id"]),
                )
                updated = _hydrate_step(await _fetchone(conn,
                    "SELECT * FROM agent_run_steps WHERE id = ?", (step["id"],)))
                await _append_event(conn, run_id, "step_updated", {"step": updated})
            await conn.execute(
                "UPDATE agent_runs SET status = ?, error = ?, completed_at = ?, result = ? WHERE id = ?",
                (status, error, ts, dumps_json(result), run_id),
            )
            run = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id = ?", (run_id,)))
            if (result or {}).get("verification"):
                run["metadata"] = {**(run.get("metadata") or {}), "verification": result["verification"]}
                await conn.execute("UPDATE agent_runs SET metadata=? WHERE id=?", (dumps_json(run["metadata"]), run_id))
                await _save_message_verification(conn, run, result["verification"])
            await _append_event(conn, run_id, "status", {"run": run})
            return run

    async def recover_orphans(
        self,
        *,
        reason: str = "orphaned by unexpected shutdown",
    ) -> dict[str, Any]:
        """Atomically close orphan runs and record recovery; never repeat tools.

        Preserve the distinction between failure and an unknown interruption.
        Only called at boot, before accepting work.
        """
        ts = now_iso()
        self._db._budget_governance_error = "startup budget recovery has not completed"
        async with self._db.transaction() as conn:
            if await _fetchone(conn, "SELECT name FROM sqlite_master WHERE type='table' AND name='budget_attempts'"):
                from .budgets import BudgetsDAO
                await BudgetsDAO(self._db).recover_in_transaction(conn)
            async with conn.execute(
                "SELECT id, session_id, status, metadata, assistant_message_id FROM agent_runs "
                "WHERE status IN ('queued', 'planning', 'running', 'awaiting_approval', "
                "'waiting_input', 'budget_paused')"
            ) as cursor:
                rows = await cursor.fetchall()
            for row in rows:
                run_id, session_id = row["id"], row["session_id"]
                metadata = loads_json(row["metadata"]) or {}
                if row["status"] == "queued" and metadata.get("cancel_requested") is True:
                    from ...verification import verification_evidence
                    engine = metadata.get("engine", "native")
                    result = {"session_id": session_id, "run_id": run_id,
                              "message_id": row["assistant_message_id"], "text": "",
                              "cancelled": True, "run_status": "cancelled", "budget_paused": False,
                              "verification": verification_evidence(status="cancelled", engine=engine)}
                    if engine == "codex":
                        result["engine"] = engine
                    profile = metadata.get("agent_execution")
                    if profile is not None:
                        from ...agent_execution import terminal_receipt
                        current = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id=?", (run_id,)))
                        result = terminal_receipt(profile, current, result, "cancelled")
                    metadata["verification"] = result["verification"]
                    await conn.execute(
                        "UPDATE agent_runs SET status='cancelled', error=?, completed_at=?, metadata=?, result=? WHERE id=?",
                        ("Cancelled before execution started", ts, dumps_json(metadata), dumps_json(result), run_id))
                    async with conn.execute(
                        "SELECT id FROM agent_run_steps WHERE run_id=? AND status IN ('pending', 'running')",
                        (run_id,),
                    ) as cursor:
                        pending_steps = await cursor.fetchall()
                    for pending in pending_steps:
                        await conn.execute(
                            "UPDATE agent_run_steps SET status='cancelled', error=?, completed_at=? WHERE id=?",
                            ("Cancelled before execution started", ts, pending["id"]))
                        step = _hydrate_step(await _fetchone(conn,
                            "SELECT * FROM agent_run_steps WHERE id=?", (pending["id"],)))
                        await _append_event(conn, run_id, "step_updated", {"step": step})
                    run = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id=?", (run_id,)))
                    await _save_message_verification(conn, run, result["verification"])
                    await _append_event(conn, run_id, "status", {"run": run})
                    continue
                await conn.execute(
                    "UPDATE agent_runs SET status = 'interrupted', error = ?, "
                    "completed_at = COALESCE(completed_at, ?) WHERE id = ?",
                    (reason, ts, run_id),
                )
                from ...verification import verification_evidence
                interrupted = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id=?", (run_id,)))
                md = interrupted.get("metadata") or {}
                receipt = loads_json((await _fetchone(conn, "SELECT result FROM agent_runs WHERE id=?", (run_id,)))["result"])
                # Codex streams each delta to its bound assistant message, but
                # writes the receipt only when the external turn finishes. A
                # crash between those writes must not hide the saved answer from
                # run.get. Reconstruct only from the exact message owned by this
                # run; the external turn and final usage remain unconfirmed.
                if receipt is None and md.get("engine") == "codex":
                    message = await _fetchone(conn,
                        "SELECT id, content, metadata FROM messages "
                        "WHERE id=? AND session_id=? AND role='assistant'",
                        (interrupted.get("assistant_message_id"), session_id))
                    message_md = loads_json(message["metadata"]) if message else None
                    if (message and isinstance(message_md, dict)
                            and message_md.get("run_id") == run_id
                            and message_md.get("engine") == "codex"):
                        receipt = {
                            "session_id": session_id, "run_id": run_id,
                            "message_id": message["id"], "engine": "codex",
                            "text": message["content"],
                            "codex_thread_id": md.get("codex_thread_id"),
                            "external_turn_id": message_md.get("external_turn_id"),
                            "tokens_in": None, "tokens_out": None, "tokens_total": None,
                            "usage_certainty": "uncertain", "iterations": None,
                            "stub": False, "partial": True, "truncated": False,
                        }
                evidence = verification_evidence(status="interrupted", base=md.get("verification"))
                md["verification"] = evidence
                if "skill_execution" in md:
                    from ...skill_execution import interrupted_receipt
                    receipt = interrupted_receipt(md["skill_execution"], interrupted, receipt)
                if "agent_execution" in md:
                    from ...agent_execution import interrupted_receipt
                    receipt = interrupted_receipt(md["agent_execution"], interrupted, receipt)
                if receipt is not None:
                    receipt.update(verification=evidence, run_status="interrupted", cancelled=False, budget_paused=False)
                await conn.execute("UPDATE agent_runs SET metadata=?,result=? WHERE id=?",
                                   (dumps_json(md), dumps_json(receipt), run_id))
                await _save_message_verification(conn, interrupted, evidence)
                # An unfinished tool may already have changed files. Keep its
                # result unknown and visible; closing a spinner is not retrying it.
                async with conn.execute(
                    "SELECT id, kind, payload FROM agent_run_steps WHERE run_id = ? "
                    "AND status IN ('pending', 'running')", (run_id,)
                ) as cursor:
                    unfinished = await cursor.fetchall()
                for pending in unfinished:
                    payload = pending["payload"]
                    error = reason
                    if pending["kind"] == "tool_call":
                        # The call may have performed its side effect before the
                        # process died. Failure here only closes the UI spinner;
                        # it is not evidence that the tool failed to execute.
                        original = _load_step_payload(payload)
                        if isinstance(original, dict):
                            details = original.copy()
                        elif original is None:
                            details = {}
                        else:
                            details = {"original_payload": original}
                        payload = dumps_json({**details, "outcome": "unknown", "requires_review": True})
                        error = f"{reason}; tool outcome unknown, review side effects"
                    await conn.execute(
                        "UPDATE agent_run_steps SET status = 'failed', error = ?, "
                        "completed_at = ?, payload = ? WHERE id = ?",
                        (error, ts, payload, pending["id"]),
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
                                 "schema_version": 1,
                                 # A budget pause stopped at a call boundary — its
                                 # effects are confirmed, so it never needs review.
                                 "requires_review": (row["status"] not in {"queued", "budget_paused"}
                                                     or any(s["kind"] == "tool_call" for s in unfinished)),
                                 "previous_status": row["status"]}), ts, ts, run_id),
                )
                step = _hydrate_step(await _fetchone(conn,
                    "SELECT * FROM agent_run_steps WHERE id = ?", (step_id,)))
                run = _hydrate_run(await _fetchone(conn,
                    "SELECT * FROM agent_runs WHERE id = ?", (run_id,)))
                await _append_event(conn, run_id, "recovered", {"run": run, "step": step})
        self._db._budget_governance_error = None
        run_ids = [row["id"] for row in rows]
        sessions = [row["session_id"] for row in rows]
        return {"recovered": len(run_ids), "run_ids": run_ids, "sessions": sessions}


async def _save_message_verification(conn: Any, run: dict[str, Any], evidence: dict[str, Any]) -> None:
    rows = await _fetchall(conn, "SELECT id,metadata FROM messages WHERE session_id=? AND role='assistant' "
                          "AND (id=? OR json_extract(metadata,'$.run_id')=?)",
                          (run["session_id"], run.get("assistant_message_id"), run["id"]))
    for row in rows:
        metadata = {**(loads_json(row["metadata"]) or {}), "verification": evidence,
                    "status": run["status"], "run_status": run["status"],
                    "partial": run["status"] != "completed" or bool((run.get("metadata") or {}).get("truncated"))}
        await conn.execute("UPDATE messages SET metadata=? WHERE id=?", (dumps_json(metadata), row["id"]))


async def link_run_message(conn: Any, run_id: str, session_id: str, message_id: str, role: str) -> None:
    """Link a saved message inside the caller's message-insert transaction."""
    run = _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id = ?", (run_id,)))
    if run is None or run["session_id"] != session_id:
        raise ValueError("message session must match its run")
    if run["status"] in {"completed", "failed", "cancelled", "interrupted"}:
        raise ValueError("cannot append a message to a terminated run")
    column = "assistant_message_id" if role == "assistant" else "user_message_id" if role == "user" else None
    if column is None or (role == "user" and run.get(column)) or run.get(column) == message_id:
        return
    await conn.execute(f"UPDATE agent_runs SET {column} = ? WHERE id = ?", (message_id, run_id))
    run[column] = message_id
    await _append_event(conn, run_id, "status", {"run": run})


async def _fetchone(conn: Any, sql: str, params: tuple = ()) -> Any:
    async with conn.execute(sql, params) as cursor:
        return await cursor.fetchone()


async def _fetchall(conn: Any, sql: str, params: tuple = ()) -> Any:
    async with conn.execute(sql, params) as cursor:
        return await cursor.fetchall()


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
    # Do not duplicate full prompts/results in every journal event or list row.
    for private in ("request", "request_hash", "result"):
        d.pop(private, None)
    return d


def _hydrate_step(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if d is None:
        return None
    d["payload"] = _load_step_payload(d.get("payload"))
    return d


def _load_step_payload(value: Any) -> Any:
    # SQLite's JSON column affinity converts JSON numeric text to a number.
    return value if isinstance(value, (int, float)) else loads_json(value)


def _duration_ms(started_at: str, completed_at: str) -> int | None:
    try:
        from datetime import datetime

        start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        return max(0, int((end - start).total_seconds() * 1000))
    except Exception:
        return None


__all__ = ["AgentRunsDAO"]
