"""Workflow storage.

Stores automation workflows with trigger config and step definitions.
Each workflow has a trigger type (webhook / schedule / agent_event),
a JSON trigger configuration, and a JSON array of steps.

v0.7.0 — Mobile Connectivity Enhancement.
"""

from __future__ import annotations

from typing import Any

from . import run_script

VERSION = 9

DDL = r"""
-- ---------------------------------------------------------------------------
-- workflows (automation workflows)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS workflows (
    id              TEXT PRIMARY KEY,           -- 'wf_' + uuid hex[:10]
    name            TEXT NOT NULL,
    description     TEXT NOT NULL DEFAULT '',
    enabled         INTEGER NOT NULL DEFAULT 1,
    trigger_type    TEXT NOT NULL,              -- 'webhook'|'schedule'|'agent_event'
    trigger_config  TEXT NOT NULL DEFAULT '{}', -- JSON
    steps           TEXT NOT NULL DEFAULT '[]', -- JSON array
    last_run_at     TEXT,
    run_count       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflows_enabled ON workflows(enabled);
CREATE INDEX IF NOT EXISTS idx_workflows_trigger ON workflows(trigger_type);
"""


def run(conn: Any) -> None:
    """Apply the workflows migration to ``conn``."""
    run_script(conn, DDL)


__all__ = ["DDL", "VERSION", "run"]
