"""Durable bounded tool output, never arbitrary paths or cross-run access."""
from __future__ import annotations

import hashlib
import json
import uuid

MAX_RECORD_BYTES = 12 * 1024 * 1024
MAX_RUN_BYTES = 32 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_DATABASE_HEADROOM = 480 * 1024 * 1024
MAX_PAGE_CHARS = 8000
STORAGE_TOOL = "__captured_output_v1__"


async def _binding(db, run_id, session_id):
    from .storage.dao.runs import AgentRunsDAO
    from .storage.dao.text_artifacts import ArtifactDAO

    run = await AgentRunsDAO(db).get_run(run_id)
    session = await db.fetchone("SELECT project_id FROM sessions WHERE id=?", (session_id,))
    meta = (run or {}).get("metadata") or {}
    if (not run or run["session_id"] != session_id or not session or not isinstance(meta, dict)
            or "project_id" not in meta or meta["project_id"] != session["project_id"]):
        raise ValueError("Run or project binding is unavailable or changed")
    return session["project_id"], await ArtifactDAO(db).service_id()


async def save(db, run_id, session_id, tool_call_id, tool_name, output):
    from .storage.dao._base import now_iso
    from .storage.dao.runs import _fetchone

    project, service = await _binding(db, run_id, session_id)
    text = json.dumps(output, ensure_ascii=False, default=str)
    data = text.encode("utf-8")
    if len(data) > MAX_RECORD_BYTES:
        raise ValueError("Captured output exceeds the storage limit")
    ref = "output_" + uuid.uuid4().hex
    digest = hashlib.sha256(data).hexdigest()
    async with db.transaction() as conn:
        run = await _fetchone(conn, "SELECT session_id,metadata,status FROM agent_runs WHERE id=?", (run_id,))
        session = await _fetchone(conn, "SELECT project_id FROM sessions WHERE id=?", (session_id,))
        identity = await _fetchone(conn, "SELECT service_id FROM artifact_service WHERE singleton=1")
        meta = json.loads(run["metadata"]) if run else None
        if (not run or run["session_id"] != session_id or run["status"] not in {"running", "planning", "awaiting_approval", "waiting_input"}
                or not session or session["project_id"] != project or not isinstance(meta, dict)
                or meta.get("project_id") != project or not identity or identity["service_id"] != service):
            raise ValueError("Run or service binding changed during output storage")
        duplicate = await _fetchone(conn, "SELECT id FROM agent_run_steps WHERE run_id=? AND tool_name=? AND tool_call_id=?", (run_id, STORAGE_TOOL, tool_call_id))
        if duplicate:
            raise ValueError("This tool call already has a captured output")
        total = await _fetchone(conn, "SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) AS total FROM agent_run_steps WHERE run_id=? AND tool_name=?", (run_id, STORAGE_TOOL))
        if total["total"] + len(data) > MAX_RUN_BYTES:
            raise ValueError("Run output storage limit reached")
        total_all = await _fetchone(conn, "SELECT COALESCE(SUM(length(CAST(payload AS BLOB))),0) AS total FROM agent_run_steps WHERE tool_name=?", (STORAGE_TOOL,))
        pages = await _fetchone(conn, "PRAGMA page_count")
        page_size = await _fetchone(conn, "PRAGMA page_size")
        if total_all["total"] + len(data) > MAX_TOTAL_BYTES or pages[0] * page_size[0] + len(data) * 2 > MAX_DATABASE_HEADROOM:
            raise ValueError("Output storage or database headroom limit reached")
        metadata = json.dumps({"project_id": project, "service_id": service, "tool_name": tool_name,
                               "bytes": len(data), "chars": len(text), "sha256": digest})
        await conn.execute("""INSERT INTO agent_run_steps
            (id,run_id,session_id,kind,status,title,summary,tool_call_id,tool_name,payload,started_at,completed_at,ordinal)
            VALUES(?,?,?,'observation','completed','Captured output',?,?,?,?,?,?,
            (SELECT COALESCE(MAX(ordinal),0)+1 FROM agent_run_steps WHERE run_id=?))""",
            (ref, run_id, session_id, metadata, tool_call_id, STORAGE_TOOL, text, now_iso(), now_iso(), run_id))
    return {"output_ref": ref, "service_id": service, "run_id": run_id, "session_id": session_id,
            "project_id": project, "bytes": len(data), "chars": len(text), "sha256": digest}


async def read(db, *, run_id, session_id, ref, service_id, offset=0, limit=MAX_PAGE_CHARS):
    if (not isinstance(offset, int) or isinstance(offset, bool) or offset < 0 or
            not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_PAGE_CHARS):
        raise ValueError("Invalid output page")
    project, identity = await _binding(db, run_id, session_id)
    if service_id != identity:
        raise ValueError("Service identity changed")
    row = await db.fetchone("""SELECT id,run_id,session_id,summary,
        length(payload) AS stored_chars,substr(payload,?,?) AS page FROM agent_run_steps
        WHERE id=? AND run_id=? AND session_id=? AND tool_name=?""",
        (offset + 1, limit, ref, run_id, session_id, STORAGE_TOOL))
    metadata = json.loads(row["summary"]) if row else None
    if (not row or not isinstance(metadata, dict) or metadata.get("project_id") != project
            or metadata.get("service_id") != identity or metadata.get("chars") != row["stored_chars"]
            or offset > row["stored_chars"]):
        raise ValueError("Output reference unavailable for this run")
    if await _binding(db, run_id, session_id) != (project, identity):
        raise ValueError("Run or service changed during output read")
    return {"id": row["id"], "run_id": run_id, "session_id": session_id, **metadata,
            "page": row["page"], "offset": offset,
            "next_offset": min(row["stored_chars"], offset + len(row["page"]))}
