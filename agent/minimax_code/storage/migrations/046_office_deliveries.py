"""Persist completed Office delivery receipts without rewriting historical data."""
from __future__ import annotations

VERSION = 46


def run(conn):
    # Historical references deliberately have no cascade on project/run deletion.
    # Bytes and parser metadata remain authoritative in attachments.
    conn.execute("""CREATE TABLE office_deliveries (
        id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        service_id TEXT NOT NULL,
        attachment_id TEXT NOT NULL UNIQUE,
        run_id TEXT,
        request_key TEXT NOT NULL,
        request_hash TEXT NOT NULL,
        input_sha256 TEXT NOT NULL,
        writer_version TEXT NOT NULL,
        validator_version INTEGER NOT NULL CHECK(validator_version=1),
        validation_report TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(project_id, request_key)
    )""")
    conn.execute("CREATE INDEX idx_office_deliveries_project "
                 "ON office_deliveries(project_id, created_at, id)")
