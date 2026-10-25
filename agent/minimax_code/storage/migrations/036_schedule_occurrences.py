"""Pinned schedules and durable, isolated execution occurrences."""

from __future__ import annotations

from . import run_script

VERSION = 36


def run(conn):
    run_script(
        conn,
        """
ALTER TABLE scheduled_jobs ADD COLUMN revision INTEGER NOT NULL DEFAULT 1;
ALTER TABLE scheduled_jobs ADD COLUMN context_version INTEGER;
ALTER TABLE scheduled_jobs ADD COLUMN context JSON;
ALTER TABLE scheduled_jobs ADD COLUMN timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai';
ALTER TABLE scheduled_jobs ADD COLUMN binding_state TEXT NOT NULL DEFAULT 'needs_rebind';
ALTER TABLE scheduled_jobs ADD COLUMN binding_reason TEXT;
ALTER TABLE scheduled_jobs ADD COLUMN deleted_at TEXT;
ALTER TABLE scheduled_jobs ADD COLUMN active_occurrence_id TEXT;
UPDATE scheduled_jobs SET enabled=0,binding_reason='Legacy schedule requires explicit context rebind';
CREATE TABLE schedule_occurrences (
 id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES scheduled_jobs(id), revision INTEGER NOT NULL,
 source TEXT NOT NULL CHECK(source IN ('cron','manual')), scheduled_for TEXT,
 coalesced_through TEXT, manual_key TEXT, fingerprint TEXT NOT NULL,
 prompt TEXT NOT NULL, context JSON NOT NULL,
 session_id TEXT NOT NULL REFERENCES sessions(id), task_id TEXT NOT NULL REFERENCES tasks(id),
 run_id TEXT REFERENCES agent_runs(id), status TEXT NOT NULL,
 reason TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, completed_at TEXT
);
CREATE UNIQUE INDEX idx_occurrence_cron ON schedule_occurrences(job_id,revision,scheduled_for) WHERE source='cron';
CREATE UNIQUE INDEX idx_occurrence_manual ON schedule_occurrences(job_id,manual_key) WHERE source='manual';
CREATE INDEX idx_occurrence_job ON schedule_occurrences(job_id,created_at DESC);
""",
    )
