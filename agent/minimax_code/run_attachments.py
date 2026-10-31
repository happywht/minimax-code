"""Record only verified attachment IDs returned by known generation tools."""
from __future__ import annotations

import hashlib
import re

from .storage.dao._base import dumps_json, loads_json, now_iso
from .storage.dao.runs import _append_event, _fetchone, _hydrate_step

TOOLS = {"export_research_sources", "generate_report_docx", "generate_report_pdf", "generate_report_pair", "generate_report_from_text",
         "generate_table_xlsx", "generate_xlsx_from_csv", "generate_xlsx_from_xlsx"}


async def record_tool_attachments(db, run_id, session_id, project_id, call, result):
    name = call.get("name")
    if name not in TOOLS or not isinstance(getattr(result, "output", None), dict) or not project_id:
        return []
    output = result.output
    if name in {"generate_report_pair", "generate_report_from_text"}:
        entries = output.get("results")
        values = [(item, item.get("format")) for item in entries if isinstance(item, dict) and item.get("status") == "saved"] if isinstance(entries, list) else []
    elif result.success:
        values = [(output, "txt" if name == "export_research_sources" else "docx" if name == "generate_report_docx" else "pdf" if name == "generate_report_pdf" else "xlsx")]
    else:
        return []
    candidates = []
    for item, kind in values[:2]:
        ident, digest = item.get("attachment_id"), item.get("sha256")
        if (kind in {"txt", "docx", "pdf", "xlsx"} and isinstance(ident, str) and re.fullmatch(r"att_[0-9a-f]{32}", ident)
                and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
            candidates.append((ident, digest, kind))
    if not candidates:
        return []
    from .ipc.handler_utils import HandlerError
    from .ipc.host_session_gate import require_host_session
    try:
        await require_host_session(session_id)
    except HandlerError:
        return []
    created = []
    async with db.transaction() as conn:
        run = await _fetchone(conn, "SELECT session_id,metadata FROM agent_runs WHERE id=?", (run_id,))
        session = await _fetchone(conn, "SELECT project_id,engine FROM sessions WHERE id=?", (session_id,))
        service = await _fetchone(conn, "SELECT service_id FROM artifact_service WHERE singleton=1")
        metadata = loads_json(run["metadata"]) if run else None
        model = metadata.get("model_snapshot") if isinstance(metadata, dict) else None
        if (not run or run["session_id"] != session_id or not session or session["project_id"] != project_id
                or session["engine"] != "native" or not isinstance(metadata, dict)
                or metadata.get("project_id") != project_id
                or not isinstance(model, dict) or model.get("engine") != "native" or not service):
            return []
        for ident, digest, kind in candidates:
            attachment = await _fetchone(conn, "SELECT id,project_id,service_id,sha256,original_name,declared_type,recognized_type FROM attachments WHERE id=?", (ident,))
            if (not attachment or attachment["project_id"] != project_id
                    or attachment["service_id"] != service["service_id"] or attachment["sha256"] != digest
                    or attachment["declared_type"] != kind
                    or attachment["recognized_type"] != ("utf8_text" if kind == "txt" else kind)):
                continue
            key = hashlib.sha256((run_id + "\0" + str(call.get("id") or "") + "\0" + ident).encode()).hexdigest()
            step_id = "run_attachment_" + key
            if await _fetchone(conn, "SELECT id FROM agent_run_steps WHERE id=?", (step_id,)):
                continue
            next_row = await _fetchone(conn, "SELECT COALESCE(MAX(ordinal),0)+1 AS n FROM agent_run_steps WHERE run_id=?", (run_id,))
            payload = {"source": "run_attachment_v1", "attachment_id": ident, "sha256": digest,
                       "service_id": service["service_id"], "project_id": project_id,
                       "filename": attachment["original_name"], "tool": name}
            now = now_iso()
            await conn.execute("INSERT INTO agent_run_steps(id,run_id,session_id,kind,status,title,summary,payload,started_at,completed_at,ordinal) VALUES(?,?,?,'observation','completed',?,?,?,?,?,?)",
                (step_id, run_id, session_id, "任务关联成果", attachment["original_name"], dumps_json(payload), now, now, next_row["n"]))
            step = _hydrate_step(await _fetchone(conn, "SELECT * FROM agent_run_steps WHERE id=?", (step_id,)))
            await _append_event(conn, run_id, "step_created", {"step": step})
            created.append(step)
    return created
