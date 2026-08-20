"""Add agent run timeline tables.

The v0.8.x chat surface streams assistant chunks and tool events, but
there is no persistent run-level structure the UI can use to render a
Claude Code style execution timeline.  This migration adds a narrow
``agent_runs`` table plus ordered ``agent_run_steps`` rows.
"""

from __future__ import annotations

from typing import Any

from . import run_script

VERSION = 12

DDL = r"""
-- ---------------------------------------------------------------------------
-- agent_runs
-- ---------------------------------------------------------------------------
CREATE TABLE agent_runs (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    mode            TEXT NOT NULL DEFAULT 'chat'
                    CHECK (mode IN ('chat', 'plan', 'execute')),
    status          TEXT NOT NULL DEFAULT 'running'
                    CHECK (status IN ('planning', 'running', 'awaiting_approval',
                                      'completed', 'failed', 'cancelled')),
    title           TEXT NOT NULL DEFAULT '',
    user_message_id TEXT,
    assistant_message_id TEXT,
    created_at      TEXT NOT NULL,
    started_at      TEXT,
    completed_at    TEXT,
    error           TEXT,
    metadata        JSON
);
CREATE INDEX idx_agent_runs_session_created
    ON agent_runs (session_id, created_at DESC);
CREATE INDEX idx_agent_runs_status
    ON agent_runs (status);

-- ---------------------------------------------------------------------------
-- agent_run_steps
-- ---------------------------------------------------------------------------
CREATE TABLE agent_run_steps (
    id            TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
    session_id    TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL
                  CHECK (kind IN ('thought', 'status', 'plan', 'tool_call',
                                  'observation', 'approval', 'patch', 'final')),
    status        TEXT NOT NULL DEFAULT 'running'
                  CHECK (status IN ('pending', 'running', 'completed',
                                    'failed', 'cancelled')),
    title         TEXT NOT NULL DEFAULT '',
    summary       TEXT NOT NULL DEFAULT '',
    tool_call_id  TEXT,
    tool_name     TEXT,
    parent_id     TEXT REFERENCES agent_run_steps(id) ON DELETE SET NULL,
    payload       JSON,
    started_at    TEXT NOT NULL,
    completed_at  TEXT,
    duration_ms   INTEGER,
    error         TEXT,
    ordinal       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_agent_run_steps_run_ordinal
    ON agent_run_steps (run_id, ordinal);
CREATE INDEX idx_agent_run_steps_tool_call_id
    ON agent_run_steps (tool_call_id);
"""


def run(conn: Any) -> None:
    """Apply the run timeline migration to ``conn``."""
    run_script(conn, DDL)


__all__ = ["DDL", "VERSION", "run"]
