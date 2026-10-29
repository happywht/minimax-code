"""Project-scoped page receipts stored in the existing durable run journal."""
from __future__ import annotations

import uuid
from typing import Any

MAX_SOURCES = 30
MAX_EXCERPT = 1000


class ResearchSources:
    def __init__(self, db=None, session_id=None, project_id=None, run_id=None) -> None:
        self._pages: dict[str, dict[str, Any]] = {}
        self.db, self.session_id, self.project_id, self.run_id = db, session_id, project_id, run_id

    async def _binding(self):
        from ...storage.dao.text_artifacts import ArtifactDAO
        if not self.db or not self.session_id or not self.project_id or not self.run_id:
            raise ValueError("Project-bound research storage is unavailable")
        identity = await ArtifactDAO(self.db).service_id()
        row = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
        project = await self.db.fetchone("SELECT id FROM projects WHERE id=?", (self.project_id,))
        if not row or row["project_id"] != self.project_id or not project:
            raise ValueError("Research session project binding changed")
        return identity

    async def remember_page(self, page):
        if self.db is None:
            return self.remember(page)
        from ...storage.dao._base import dumps_json, loads_json, now_iso
        from ...storage.dao.runs import _append_event, _fetchone, _hydrate_step

        identity = await self._binding()
        ident = "page_" + uuid.uuid4().hex
        payload = {"source": "research_page_v1", "service_id": identity, "project_id": self.project_id,
                   "page": {key: page[key] for key in ("title", "url", "accessed_at", "excerpt", "truncated",
                                                       "title_truncated", "excerpt_truncated")}}
        async with self.db.transaction() as conn:
            session = await _fetchone(conn, "SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
            run = await _fetchone(conn, "SELECT session_id,metadata,status FROM agent_runs WHERE id=?", (self.run_id,))
            service = await _fetchone(conn, "SELECT service_id FROM artifact_service WHERE singleton=1")
            md = loads_json(run["metadata"]) if run else None
            if (not session or session["project_id"] != self.project_id or not run
                    or run["session_id"] != self.session_id or not isinstance(md, dict)
                    or md.get("project_id") != self.project_id
                    or run["status"] not in {"running", "planning", "awaiting_approval", "waiting_input"}
                    or not service or service["service_id"] != identity):
                raise ValueError("Research run, project or service binding changed")
            ordinal_row = await _fetchone(conn, "SELECT COALESCE(MAX(ordinal),0)+1 AS n FROM agent_run_steps WHERE run_id=?", (self.run_id,))
            now = now_iso()
            await conn.execute("INSERT INTO agent_run_steps(id,run_id,session_id,kind,status,title,summary,payload,started_at,completed_at,ordinal) VALUES(?,?,?,'observation','completed',?,?,?,?,?,?)",
                (ident, self.run_id, self.session_id, "已读取研究来源", page["url"], dumps_json(payload), now, now, ordinal_row["n"]))
            step = _hydrate_step(await _fetchone(conn, "SELECT * FROM agent_run_steps WHERE id=?", (ident,)))
            await _append_event(conn, self.run_id, "step_created", {"step": step})
        return ident

    async def list_pages(self, query="", limit=20):
        import json
        identity = await self._binding()
        if not isinstance(query, str) or len(query) > 200 or not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 20:
            raise ValueError("Query must be at most 200 characters and limit 1–20")
        term = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = await self.db.fetchall("""SELECT s.id,s.run_id,s.payload FROM agent_run_steps s
            JOIN agent_runs r ON r.id=s.run_id
            WHERE s.kind='observation' AND s.status='completed' AND json_valid(s.payload)
            AND json_valid(r.metadata) AND json_extract(r.metadata,'$.project_id')=?
            AND json_extract(s.payload,'$.source')='research_page_v1'
            AND json_extract(s.payload,'$.project_id')=? AND json_extract(s.payload,'$.service_id')=?
            AND (json_extract(s.payload,'$.page.title') LIKE ? ESCAPE '\\'
              OR json_extract(s.payload,'$.page.url') LIKE ? ESCAPE '\\'
              OR json_extract(s.payload,'$.page.excerpt') LIKE ? ESCAPE '\\')
            ORDER BY s.started_at DESC,s.id DESC LIMIT ?""",
            (self.project_id, self.project_id, identity, term, term, term, limit))
        return [{"page_source_id": row["id"], "run_id": row["run_id"],
                 **self._report_page(json.loads(row["payload"])["page"])} for row in rows]

    @staticmethod
    def _report_page(page):
        title, excerpt = page["title"], page["excerpt"]
        return {"title": title[:200] if title else "（页面未提供标题）",
                "title_truncated": bool(page.get("title_truncated") or len(title) > 200),
                "url": page["url"], "accessed_at": page["accessed_at"],
                "excerpt": excerpt[:MAX_EXCERPT] or None,
                "excerpt_truncated": bool(page.get("excerpt_truncated", page["truncated"]) or len(excerpt) > MAX_EXCERPT)}

    async def resolve_pages(self, source_ids, manual):
        if self.db is None:
            return self.resolve(source_ids, manual)
        from ...storage.dao._base import loads_json
        identity = await self._binding()
        if source_ids is None:
            source_ids = []
        if (not isinstance(source_ids, list) or len(source_ids) > MAX_SOURCES
                or any(not isinstance(value, str) for value in source_ids)
                or len(set(source_ids)) != len(source_ids)):
            raise ValueError("page_source_ids must contain at most 30 unique source IDs")
        result = list(manual or [])
        for ident in source_ids:
            row = await self.db.fetchone("""SELECT s.payload FROM agent_run_steps s JOIN agent_runs r ON r.id=s.run_id
                WHERE s.id=? AND s.status='completed' AND s.kind='observation' AND json_valid(s.payload)
                AND json_valid(r.metadata) AND json_extract(r.metadata,'$.project_id')=?""", (ident, self.project_id))
            payload = loads_json(row["payload"]) if row else None
            if (not isinstance(payload, dict) or payload.get("source") != "research_page_v1"
                    or payload.get("project_id") != self.project_id or payload.get("service_id") != identity):
                raise ValueError("Unknown page source ID for this project and service")
            result.append(self._report_page(payload["page"]))
        if len(result) > MAX_SOURCES:
            raise ValueError("A report can contain at most 30 sources")
        return result

    def remember(self, page: dict[str, Any]) -> str | None:
        if len(self._pages) >= MAX_SOURCES:
            return None
        source_id = "page_" + uuid.uuid4().hex
        title = page["title"]
        excerpt = page["excerpt"]
        self._pages[source_id] = {
            "title": title[:200] if title else "（页面未提供标题）",
            "title_truncated": len(title) > 200,
            "url": page["url"], "accessed_at": page["accessed_at"],
            "excerpt": excerpt[:MAX_EXCERPT] or None,
            "excerpt_truncated": bool(page["truncated"] or len(excerpt) > MAX_EXCERPT),
        }
        return source_id

    def resolve(self, source_ids: list[str] | None, manual: list[dict[str, Any]] | None):
        if source_ids is None:
            source_ids = []
        if (not isinstance(source_ids, list) or len(source_ids) > MAX_SOURCES
                or any(not isinstance(value, str) for value in source_ids)
                or len(set(source_ids)) != len(source_ids)):
            raise ValueError("page_source_ids must contain at most 30 unique source IDs")
        if any(value not in self._pages for value in source_ids):
            raise ValueError("Unknown page source ID; read the page in this task first")
        combined = list(manual or []) + [self._pages[value].copy() for value in source_ids]
        if len(combined) > MAX_SOURCES:
            raise ValueError("A report can contain at most 30 sources")
        return combined
