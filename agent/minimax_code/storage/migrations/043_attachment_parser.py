"""Versioned parser scope/reason; preserve all historical v1 records unchanged."""
from __future__ import annotations

VERSION = 43


def run(conn):
    conn.execute("ALTER TABLE attachments RENAME TO attachments_v1")
    conn.execute("DROP INDEX idx_attachments_project")
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
        parser_version INTEGER NOT NULL CHECK(parser_version IN (1,2)),
        preview_text TEXT NOT NULL,
        preview_truncated INTEGER NOT NULL CHECK(preview_truncated IN (0,1)),
        quality_status TEXT NOT NULL CHECK(quality_status='unverified'),
        created_at TEXT NOT NULL,
        parser_reason TEXT NOT NULL,
        parser_scope TEXT NOT NULL,
        CHECK((parser_version=1 AND parser_reason='legacy_v1' AND parser_scope='legacy_v1')
              OR (parser_version=2 AND parser_scope IN
                  ('inert_text','docx_body_paragraphs_tables','original_only'))),
        UNIQUE(project_id, original_name, version)
    )""")
    conn.execute("INSERT INTO attachments SELECT *, 'legacy_v1', 'legacy_v1' FROM attachments_v1")
    conn.execute("DROP TABLE attachments_v1")
    conn.execute("CREATE INDEX idx_attachments_project ON attachments(project_id,created_at,id)")
