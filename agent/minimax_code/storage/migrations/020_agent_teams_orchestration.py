"""Extend agent_teams for v0.11.0 orchestration modes.

Adds a JSON ``orchestration_config`` column for mode-specific options
(e.g. ``review_agent`` for review mode) and widens the
``orchestration_mode`` CHECK constraint to include ``vote`` and
``review``.

v0.11.0 — Agent Studio orchestration enhancements.
"""

from __future__ import annotations

from typing import Any

VERSION = 20


def run(conn: Any) -> None:
    """Apply the agent_teams orchestration migration to ``conn``."""
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(agent_teams)").fetchall()
    }

    if "orchestration_config" not in existing:
        conn.execute("ALTER TABLE agent_teams ADD COLUMN orchestration_config TEXT")

    # SQLite cannot drop or alter a CHECK constraint, so we rebuild the
    # table when the existing check does not yet allow vote/review.
    # agent_teams has no inbound foreign keys, so this is safe.
    if _check_is_widened(conn):
        return

    # Clear any _new leftover from a previous failed run of this migration.
    conn.execute("DROP TABLE IF EXISTS agent_teams_new")
    conn.execute(
        """
        CREATE TABLE agent_teams_new (
            id                   TEXT PRIMARY KEY,
            name                 TEXT NOT NULL UNIQUE,
            description          TEXT NOT NULL DEFAULT '',
            icon                 TEXT NOT NULL DEFAULT '',
            color                TEXT NOT NULL DEFAULT '',
            agents               TEXT NOT NULL DEFAULT '[]',
            orchestration_mode   TEXT NOT NULL DEFAULT 'parallel'
                                 CHECK (orchestration_mode IN ('parallel','sequential','round-robin','vote','review')),
            orchestration_config TEXT,
            enabled              INTEGER NOT NULL DEFAULT 1,
            created_at           TEXT NOT NULL,
            updated_at           TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_teams_enabled_new ON agent_teams_new(enabled)"
    )

    base_cols = [
        "id",
        "name",
        "description",
        "icon",
        "color",
        "agents",
        "orchestration_mode",
        "enabled",
        "created_at",
        "updated_at",
    ]
    has_config = "orchestration_config" in existing
    if has_config:
        base_cols.append("orchestration_config")

    col_sql = ", ".join(base_cols)
    conn.execute(
        f"INSERT INTO agent_teams_new ({col_sql}) SELECT {col_sql} FROM agent_teams"
    )
    conn.execute("DROP TABLE agent_teams")
    conn.execute("ALTER TABLE agent_teams_new RENAME TO agent_teams")


def _check_is_widened(conn: Any) -> bool:
    """Return True if the existing agent_teams check already allows vote/review."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_teams'"
    ).fetchone()
    if not row or not row[0]:
        return False
    sql = row[0].lower()
    return "'vote'" in sql and "'review'" in sql


__all__ = ["VERSION", "run"]
