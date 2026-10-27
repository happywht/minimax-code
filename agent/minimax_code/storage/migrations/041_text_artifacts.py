"""Immutable, explicitly registered text artifacts; historical refs are retained."""
from __future__ import annotations

VERSION = 41


def run(conn):
    conn.execute("""CREATE TABLE artifact_service (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),
        service_id TEXT NOT NULL UNIQUE
    )""")
    # Historical IDs intentionally do not cascade on project/run deletion.
    conn.execute("""CREATE TABLE text_artifacts (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        run_id TEXT,
        service_id TEXT NOT NULL,
        source_path TEXT NOT NULL,
        snapshot_id TEXT NOT NULL UNIQUE,
        mime_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL CHECK(size_bytes >= 0 AND size_bytes <= 2097152),
        sha256 TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version > 0),
        parse_status TEXT NOT NULL CHECK(parse_status='parseable'),
        verification_status TEXT NOT NULL CHECK(verification_status='unverified'),
        created_at TEXT NOT NULL,
        UNIQUE(project_id, source_path, version)
    )""")
    conn.execute("CREATE INDEX idx_artifacts_project ON text_artifacts(project_id, created_at, id)")
