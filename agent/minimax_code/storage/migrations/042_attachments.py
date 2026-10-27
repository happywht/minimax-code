"""Immutable project attachment originals, separate from generated text artifacts."""
from __future__ import annotations

VERSION = 42


def run(conn):
    conn.execute("""CREATE TABLE attachments (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        service_id TEXT NOT NULL,
        snapshot_id TEXT NOT NULL UNIQUE,
        original_name TEXT NOT NULL CHECK(length(original_name) BETWEEN 1 AND 240),
        declared_type TEXT NOT NULL,
        recognized_type TEXT NOT NULL,
        size_bytes INTEGER NOT NULL CHECK(size_bytes BETWEEN 0 AND 10485760),
        sha256 TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version > 0),
        parser_status TEXT NOT NULL CHECK(parser_status IN
            ('parsed','empty','truncated','parse_failed','type_mismatch',
             'parser_unavailable','ocr_not_attempted')),
        parser_version INTEGER NOT NULL CHECK(parser_version=1),
        preview_text TEXT NOT NULL,
        preview_truncated INTEGER NOT NULL CHECK(preview_truncated IN (0,1)),
        quality_status TEXT NOT NULL CHECK(quality_status='unverified'),
        created_at TEXT NOT NULL,
        UNIQUE(project_id, original_name, version)
    )""")
    # Retain historical originals on project deletion, matching text artifacts.
    conn.execute("CREATE INDEX idx_attachments_project ON attachments(project_id,created_at,id)")
