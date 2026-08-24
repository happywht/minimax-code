"""Widen agent_runs.mode to support the tool-path sub-agent lifecycle.

v1.4.0 persists every ``spawn_subagent`` tool call as an
``agent_runs`` row with ``mode='subagent'`` so sub-agents spawned
from the tool path are visible in the run timeline (and survive
agent restarts). SQLite cannot alter a CHECK constraint, so we
rebuild ``agent_runs`` with the widened check while preserving
existing rows — the same four-step rebuild as migration 021.

v1.4.0 — sub-agent lifecycle track.
"""

from __future__ import annotations

from typing import Any

VERSION = 28


def run(conn: Any) -> None:
    """Apply the agent_runs mode widening migration to ``conn``."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_runs'"
    ).fetchone()
    if not row or not row[0]:
        return

    sql = row[0].lower()
    if "'subagent'" in sql:
        return

    # Clear any _new leftover from a previous failed run of this migration.
    conn.execute("DROP TABLE IF EXISTS agent_runs_new")
    conn.execute(
        """
        CREATE TABLE agent_runs_new (
            id              TEXT PRIMARY KEY,
            session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            mode            TEXT NOT NULL DEFAULT 'chat'
                            CHECK (mode IN ('chat', 'plan', 'execute', 'team', 'subagent')),
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
        )
        """
    )
    # Index names carry a _v28 suffix: the plain-``_new`` names used by
    # migration 021 still exist on the OLD table at this point (they
    # survived its RENAME), so ``IF NOT EXISTS`` with those names would
    # silently skip — and the subsequent DROP TABLE would delete them,
    # leaving the rebuilt table unindexed. Fresh names sidestep both.
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_runs_session_created_v28
            ON agent_runs_new (session_id, created_at DESC)
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_status_v28 ON agent_runs_new (status)"
    )

    columns = [
        "id",
        "session_id",
        "mode",
        "status",
        "title",
        "user_message_id",
        "assistant_message_id",
        "created_at",
        "started_at",
        "completed_at",
        "error",
        "metadata",
    ]
    col_sql = ", ".join(columns)
    conn.execute(
        f"INSERT INTO agent_runs_new ({col_sql}) SELECT {col_sql} FROM agent_runs"
    )
    conn.execute("DROP TABLE agent_runs")
    conn.execute("ALTER TABLE agent_runs_new RENAME TO agent_runs")


__all__ = ["VERSION", "run"]
