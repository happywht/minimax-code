"""Widen agent_runs.mode to support team orchestration.

v0.11.0 stores the result of ``team.spawn`` in ``agent_runs`` with
``mode='team'``. SQLite cannot alter a CHECK constraint, so we rebuild
``agent_runs`` with the widened check while preserving existing rows.

v0.11.0 — Agent Studio orchestration enhancements.
"""

from __future__ import annotations

from typing import Any

VERSION = 21


def run(conn: Any) -> None:
    """Apply the agent_runs mode widening migration to ``conn``."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_runs'"
    ).fetchone()
    if not row or not row[0]:
        return

    sql = row[0].lower()
    if "'team'" in sql:
        return

    # Clear any _new leftover from a previous failed run of this migration.
    conn.execute("DROP TABLE IF EXISTS agent_runs_new")
    conn.execute(
        """
        CREATE TABLE agent_runs_new (
            id              TEXT PRIMARY KEY,
            session_id      TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            mode            TEXT NOT NULL DEFAULT 'chat'
                            CHECK (mode IN ('chat', 'plan', 'execute', 'team')),
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
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_agent_runs_session_created_new
            ON agent_runs_new (session_id, created_at DESC)
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_runs_status_new ON agent_runs_new (status)"
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
