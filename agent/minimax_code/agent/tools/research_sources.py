"""Project-scoped page receipts retained independently of their run journal."""
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
        if not self.db or not self.session_id or not self.project_id:
            raise ValueError("Project-bound research storage is unavailable")
        identity = await ArtifactDAO(self.db).service_id()
        row = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
        project = await self.db.fetchone("SELECT id FROM projects WHERE id=?", (self.project_id,))
        if not row or row["project_id"] != self.project_id or not project:
            raise ValueError("Research session project binding changed")
        return identity

    async def remember_page(self, page):
        from ...execution import require_write_access

        require_write_access()
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
            await conn.execute("INSERT INTO research_sources VALUES(?,?,?,?,?,?)",
                (ident, self.project_id, identity, self.run_id, dumps_json(payload), now))
            step = _hydrate_step(await _fetchone(conn, "SELECT * FROM agent_run_steps WHERE id=?", (ident,)))
            await _append_event(conn, self.run_id, "step_created", {"step": step})
        return ident

    async def remember_manual_page(self, page, expected_service_id):
        """Commit a user-initiated read as its own completed, explicitly labelled run."""
        from ...execution import require_write_access
        from ...storage.dao._base import dumps_json, now_iso
        from ...storage.dao.runs import _append_event, _fetchone, _hydrate_run, _hydrate_step
        require_write_access()
        if await self._binding() != expected_service_id:
            raise ValueError("Research service identity changed")
        run_id, ident, now = "run_" + uuid.uuid4().hex, "page_" + uuid.uuid4().hex, now_iso()
        payload = {"source": "research_page_v1", "service_id": expected_service_id, "project_id": self.project_id,
                   "page": {key: page[key] for key in ("title", "url", "accessed_at", "excerpt", "truncated", "title_truncated", "excerpt_truncated")}}
        async with self.db.transaction() as conn:
            session = await _fetchone(conn, "SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
            project = await _fetchone(conn, "SELECT id FROM projects WHERE id=?", (self.project_id,))
            service = await _fetchone(conn, "SELECT service_id FROM artifact_service WHERE singleton=1")
            if not session or session["project_id"] != self.project_id or not project or not service or service["service_id"] != expected_service_id:
                raise ValueError("Research session, project or service binding changed")
            await conn.execute("INSERT INTO agent_runs(id,session_id,mode,status,title,created_at,started_at,completed_at,metadata) VALUES(?,?,'chat','completed',?,?,?,?,?)",
                (run_id, self.session_id, "用户手动读取网页", now, now, now, dumps_json({"project_id": self.project_id, "manual_research": True})))
            await _append_event(conn, run_id, "created", {"run": _hydrate_run(await _fetchone(conn, "SELECT * FROM agent_runs WHERE id=?", (run_id,)))})
            await conn.execute("INSERT INTO agent_run_steps(id,run_id,session_id,kind,status,title,summary,payload,started_at,completed_at,ordinal) VALUES(?,?,?,'observation','completed',?,?,?,?,?,1)",
                (ident, run_id, self.session_id, "用户手动读取研究来源", page["url"], dumps_json(payload), now, now))
            await conn.execute("INSERT INTO research_sources VALUES(?,?,?,?,?,?)",
                (ident, self.project_id, expected_service_id, run_id, dumps_json(payload), now))
            await _append_event(conn, run_id, "step_created", {"step": _hydrate_step(await _fetchone(conn, "SELECT * FROM agent_run_steps WHERE id=?", (ident,)))})
        return ident, run_id

    async def list_pages(self, query="", limit=20, offset=0):
        import json
        identity = await self._binding()
        if (not isinstance(query, str) or len(query) > 200 or not isinstance(limit, int)
                or isinstance(limit, bool) or not 1 <= limit <= 20 or not isinstance(offset, int)
                or isinstance(offset, bool) or not 0 <= offset <= 10000):
            raise ValueError("Query must be at most 200 characters and limit 1–20")
        term = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        rows = await self.db.fetchall("""SELECT id,run_id,payload FROM research_sources
            WHERE project_id=? AND service_id=?
            AND (json_extract(payload,'$.page.title') LIKE ? ESCAPE '\\'
              OR json_extract(payload,'$.page.url') LIKE ? ESCAPE '\\'
              OR json_extract(payload,'$.page.excerpt') LIKE ? ESCAPE '\\')
            ORDER BY recorded_at DESC,id DESC LIMIT ? OFFSET ?""",
            (self.project_id, identity, term, term, term, limit, offset))
        results = []
        for row in rows:
            page = json.loads(row["payload"])["page"]
            result = {"page_source_id": row["id"], "run_id": row["run_id"],
                      **self._report_page(page)}
            # SQLite LIKE folds ASCII case only. Preserve character offsets while
            # applying the same folding to find the first excerpt match.
            if query:
                fold = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
                excerpt = page["excerpt"]
                position = excerpt.translate(fold).find(query.translate(fold))
                if position >= 0:
                    start = max(0, position - 120)
                    end = min(len(excerpt), position + len(query) + 120)
                    result["excerpt_match"] = {"text": excerpt[start:end], "start": start,
                                               "end": end, "match_start": position,
                                               "match_end": position + len(query)}
            results.append(result)
        return results

    async def get_page(self, source_id):
        from ...storage.dao._base import loads_json
        identity = await self._binding()
        row = await self.db.fetchone("""SELECT id,run_id,payload FROM research_sources
            WHERE id=? AND project_id=? AND service_id=?""", (source_id, self.project_id, identity))
        payload = loads_json(row["payload"]) if row else None
        if (not isinstance(payload, dict) or payload.get("source") != "research_page_v1"
                or payload.get("project_id") != self.project_id or payload.get("service_id") != identity):
            raise ValueError("Unknown page source ID for this project and service")
        return {"page_source_id": row["id"], "run_id": row["run_id"], **payload["page"]}

    @staticmethod
    def _report_page(page, span=None):
        title, excerpt = page["title"], page["excerpt"]
        start, end = (0, min(len(excerpt), MAX_EXCERPT)) if span is None else span
        return {"title": title[:200] if title else "（页面未提供标题）",
                "title_truncated": bool(page.get("title_truncated") or len(title) > 200),
                "url": page["url"], "accessed_at": page["accessed_at"],
                "excerpt": excerpt[start:end] or None,
                "excerpt_truncated": bool(page.get("excerpt_truncated", page["truncated"]) or start > 0 or end < len(excerpt))}

    async def resolve_pages(self, source_ids, manual, excerpt_ranges=None):
        if self.db is None:
            if excerpt_ranges:
                raise ValueError("Page excerpt ranges require project-bound research storage")
            return self.resolve(source_ids, manual)
        from ...storage.dao._base import loads_json
        identity = await self._binding()
        if source_ids is None:
            source_ids = []
        if (not isinstance(source_ids, list) or len(source_ids) > MAX_SOURCES
                or any(not isinstance(value, str) for value in source_ids)
                or len(set(source_ids)) != len(source_ids)):
            raise ValueError("page_source_ids must contain at most 30 unique source IDs")
        if excerpt_ranges is None:
            excerpt_ranges = []
        if not isinstance(excerpt_ranges, list) or len(excerpt_ranges) > MAX_SOURCES:
            raise ValueError("page_excerpt_ranges must contain at most 30 ranges")
        ranges = {}
        for ref in excerpt_ranges:
            if (not isinstance(ref, dict) or set(ref) != {"page_source_id", "start", "end"}
                    or not isinstance(ref["page_source_id"], str)
                    or ref["page_source_id"] not in source_ids or ref["page_source_id"] in ranges
                    or type(ref["start"]) is not int or type(ref["end"]) is not int
                    or not 0 <= ref["start"] < ref["end"] <= 8000
                    or ref["end"] - ref["start"] > MAX_EXCERPT):
                raise ValueError("Invalid page excerpt range or source ID")
            ranges[ref["page_source_id"]] = (ref["start"], ref["end"])
        result = list(manual or [])
        for ident in source_ids:
            row = await self.db.fetchone("""SELECT payload FROM research_sources
                WHERE id=? AND project_id=? AND service_id=?""", (ident, self.project_id, identity))
            payload = loads_json(row["payload"]) if row else None
            if (not isinstance(payload, dict) or payload.get("source") != "research_page_v1"
                    or payload.get("project_id") != self.project_id or payload.get("service_id") != identity):
                raise ValueError("Unknown page source ID for this project and service")
            span = ranges.get(ident)
            if span is not None and span[1] > len(payload["page"]["excerpt"]):
                raise ValueError("Page excerpt range exceeds the saved excerpt")
            result.append(self._report_page(payload["page"], span))
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
