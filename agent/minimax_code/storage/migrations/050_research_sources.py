"""Retain verified-origin page receipts independently of run/session lifetime."""
from __future__ import annotations

import json
import logging

VERSION = 50
logger = logging.getLogger(__name__)


def run(conn):
    conn.execute("""CREATE TABLE research_sources (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        service_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        payload TEXT NOT NULL,
        recorded_at TEXT NOT NULL
    )""")
    conn.execute("CREATE INDEX idx_research_sources_project ON research_sources(project_id, service_id, recorded_at DESC, id DESC)")
    # Only migrate receipts whose run, current session, project and service agree.
    # Leave every historical journal row untouched, including skipped rows.
    rows = conn.execute("""SELECT s.id,s.run_id,s.payload,s.started_at,r.metadata,
        sess.project_id FROM agent_run_steps s JOIN agent_runs r ON r.id=s.run_id
        JOIN sessions sess ON sess.id=r.session_id
        WHERE s.kind='observation' AND s.status='completed' AND json_valid(s.payload)
        AND json_extract(s.payload,'$.source')='research_page_v1'""").fetchall()
    service = conn.execute("SELECT service_id FROM artifact_service WHERE singleton=1").fetchone()
    inserted = skipped = 0
    for ident, run_id, raw, recorded_at, metadata, session_project in rows:
        try:
            payload, md = json.loads(raw), json.loads(metadata or 'null')
            project, identity, page = payload.get('project_id'), payload.get('service_id'), payload.get('page')
            valid_page = isinstance(page, dict) and all(isinstance(page.get(k), str) for k in ('title', 'url', 'accessed_at', 'excerpt')) and all(isinstance(page.get(k), bool) for k in ('truncated', 'title_truncated', 'excerpt_truncated'))
            valid = (isinstance(md, dict) and isinstance(project, str) and project
                and service and identity == service[0] and md.get('project_id') == project
                and session_project == project and valid_page
                and conn.execute('SELECT 1 FROM projects WHERE id=?', (project,)).fetchone())
            if not valid:
                skipped += 1
                continue
            conn.execute('INSERT INTO research_sources VALUES(?,?,?,?,?,?)',
                (ident, project, identity, run_id, raw, recorded_at))
            inserted += 1
        except (TypeError, ValueError, AttributeError):
            skipped += 1
    logger.info('Research source migration: retained %d receipts; skipped %d with uncertain or invalid ownership/content', inserted, skipped)
