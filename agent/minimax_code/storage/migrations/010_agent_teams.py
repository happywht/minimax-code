"""Agent teams + extended agent columns.

Creates the ``agent_teams`` table for team-based orchestration and
adds description, icon, color, category, tags, team_id, skills,
max_iterations, temperature, updated_at columns to the existing
``agents`` table.

v0.8.0 — Enterprise Multi-Agent.
"""

from __future__ import annotations

from typing import Any

from . import run_script

VERSION = 10

DDL = r"""
-- ---------------------------------------------------------------------------
-- agent_teams (team templates for multi-agent orchestration)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_teams (
    id                 TEXT PRIMARY KEY,           -- 'team_' + uuid hex[:10]
    name               TEXT NOT NULL UNIQUE,
    description        TEXT NOT NULL DEFAULT '',
    icon               TEXT NOT NULL DEFAULT '',
    color              TEXT NOT NULL DEFAULT '',
    agents             TEXT NOT NULL DEFAULT '[]', -- JSON: array of agent names
    orchestration_mode TEXT NOT NULL DEFAULT 'parallel'
                       CHECK (orchestration_mode IN ('parallel','sequential','round-robin')),
    enabled            INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_agent_teams_enabled ON agent_teams(enabled);

-- ---------------------------------------------------------------------------
-- agents table extensions (v0.8.0)
-- ---------------------------------------------------------------------------
"""


def run(conn: Any) -> None:
    """Apply the agent_teams migration to ``conn``."""
    run_script(conn, DDL)

    # Add new columns to agents table — use ALTER TABLE with
    # IF-not-exists guard via a pragma trick (SQLite doesn't have
    # native ADD COLUMN IF NOT EXISTS, so we check first).
    _alter_agents(conn)


def _alter_agents(conn: Any) -> None:
    """Add v0.8.0 columns to the agents table (idempotent)."""
    # Get current column names
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(agents)").fetchall()
    }

    additions = [
        ("description", "TEXT NOT NULL DEFAULT ''"),
        ("enabled", "INTEGER NOT NULL DEFAULT 1"),
        ("icon", "TEXT NOT NULL DEFAULT ''"),
        ("color", "TEXT NOT NULL DEFAULT ''"),
        ("category", "TEXT NOT NULL DEFAULT ''"),
        ("tags", "TEXT"),             # JSON array
        ("team_id", "TEXT"),
        ("skills", "TEXT"),           # JSON array of skill names
        ("max_iterations", "INTEGER NOT NULL DEFAULT 8"),
        ("temperature", "REAL"),
        ("updated_at", "TEXT"),
    ]

    for col_name, col_type in additions:
        if col_name not in existing:
            conn.execute(
                f"ALTER TABLE agents ADD COLUMN {col_name} {col_type}"
            )


__all__ = ["DDL", "VERSION", "run"]
