"""Initial schema.

Creates every persistent table the agent needs for Phase 1:

* ``sessions``              — conversation metadata
* ``messages``              — chat messages, with tool-call & token accounting
* ``tasks``                 — long-running task tracking
* ``skills``                — skill registry
* ``scheduled_jobs``        — cron-scheduled jobs
* ``agents``                — sub-agent configurations
* ``permission_rules``      — tool-call authorization policies
* ``mobile_devices``        — paired phones / tablets
* ``schema_migrations``     — bookkeeping (added by ``ensure_migration_table``)

Indexes (right after each table) cover the dominant access patterns
identified in the architecture doc — listing messages for a session,
listing tasks for a session, finding enabled skills, looking up
permission rules by tool pattern, finding paired devices, and the
"next jobs to fire" query in the scheduler.
"""

from __future__ import annotations

from typing import Any

from . import run_script

VERSION = 1


DDL = r"""
-- ---------------------------------------------------------------------------
-- sessions
-- ---------------------------------------------------------------------------
CREATE TABLE sessions (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    archived      INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    model         TEXT,
    system_prompt TEXT
);
CREATE INDEX idx_sessions_archived_updated
    ON sessions (archived, updated_at DESC);
CREATE INDEX idx_sessions_updated
    ON sessions (updated_at DESC);

-- ---------------------------------------------------------------------------
-- messages
-- ---------------------------------------------------------------------------
CREATE TABLE messages (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role          TEXT NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    content       TEXT NOT NULL DEFAULT '',
    tool_calls    JSON,
    tool_call_id  TEXT,
    parent_id     TEXT REFERENCES messages(id) ON DELETE SET NULL,
    created_at    TEXT NOT NULL,
    tokens_in     INTEGER NOT NULL DEFAULT 0,
    tokens_out    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_messages_session_created
    ON messages (session_id, created_at);
CREATE INDEX idx_messages_parent
    ON messages (parent_id);
CREATE INDEX idx_messages_tool_call_id
    ON messages (tool_call_id);

-- ---------------------------------------------------------------------------
-- tasks
-- ---------------------------------------------------------------------------
CREATE TABLE tasks (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    title         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    progress      INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    created_at    TEXT NOT NULL,
    started_at    TEXT,
    completed_at  TEXT,
    error         TEXT
);
CREATE INDEX idx_tasks_session_created
    ON tasks (session_id, created_at DESC);
CREATE INDEX idx_tasks_status
    ON tasks (status);

-- ---------------------------------------------------------------------------
-- skills
-- ---------------------------------------------------------------------------
CREATE TABLE skills (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    version       TEXT NOT NULL DEFAULT '0.0.0',
    path          TEXT NOT NULL,
    description   TEXT NOT NULL DEFAULT '',
    when_to_use   TEXT NOT NULL DEFAULT '',
    enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE INDEX idx_skills_enabled
    ON skills (enabled);
CREATE INDEX idx_skills_name
    ON skills (name);

-- ---------------------------------------------------------------------------
-- scheduled_jobs
-- ---------------------------------------------------------------------------
CREATE TABLE scheduled_jobs (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    cron_expr     TEXT NOT NULL,
    payload       JSON,
    enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    last_run_at   TEXT,
    next_run_at   TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX idx_jobs_enabled_next_run
    ON scheduled_jobs (enabled, next_run_at);
CREATE INDEX idx_jobs_name
    ON scheduled_jobs (name);

-- ---------------------------------------------------------------------------
-- agents (sub-agent configurations)
-- ---------------------------------------------------------------------------
CREATE TABLE agents (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE,
    system_prompt TEXT NOT NULL DEFAULT '',
    tool_allowlist JSON,
    model         TEXT,
    created_at    TEXT NOT NULL
);
CREATE INDEX idx_agents_name
    ON agents (name);

-- ---------------------------------------------------------------------------
-- permission_rules
-- ---------------------------------------------------------------------------
CREATE TABLE permission_rules (
    id            TEXT PRIMARY KEY,
    tool_pattern  TEXT NOT NULL,
    action        TEXT NOT NULL CHECK (action IN ('allow', 'deny', 'ask')),
    scope         TEXT NOT NULL DEFAULT 'global',
    created_at    TEXT NOT NULL
);
CREATE INDEX idx_permissions_tool_pattern
    ON permission_rules (tool_pattern);
CREATE INDEX idx_permissions_action
    ON permission_rules (action);

-- ---------------------------------------------------------------------------
-- mobile_devices
-- ---------------------------------------------------------------------------
CREATE TABLE mobile_devices (
    id            TEXT PRIMARY KEY,
    device_id     TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    public_key    TEXT NOT NULL,
    paired_at     TEXT NOT NULL,
    last_seen_at  TEXT
);
CREATE INDEX idx_devices_device_id
    ON mobile_devices (device_id);
CREATE INDEX idx_devices_last_seen
    ON mobile_devices (last_seen_at);
"""


def run(conn: Any) -> None:
    """Apply the initial schema to ``conn``.

    ``conn`` may be a stdlib ``sqlite3.Connection`` or an
    ``aiosqlite.Connection`` — :func:`run_script` executes the DDL
    one statement at a time on either.
    """
    run_script(conn, DDL)
