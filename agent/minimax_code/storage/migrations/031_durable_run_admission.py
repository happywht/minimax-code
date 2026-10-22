"""Durable request admission and explicit interrupted/queued run states.

The runner follows SQLite's generalized ALTER TABLE procedure: foreign keys
are disabled before BEGIN, checked before COMMIT, and restored afterwards.
See https://www.sqlite.org/lang_altertable.html#otheralter .
"""

from __future__ import annotations

from typing import Any

VERSION = 31
DISABLE_FOREIGN_KEYS = True


def run(conn: Any) -> None:
    if conn.execute("PRAGMA foreign_keys").fetchone()[0]:
        raise RuntimeError("Run schema rebuild must use the migration runner")
    # Preserve attached user indexes/triggers as well as product indexes.
    objects = conn.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name = 'agent_runs' "
        "AND type IN ('index', 'trigger') AND sql IS NOT NULL"
    ).fetchall()
    conn.execute("""
        CREATE TABLE agent_runs_v31 (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            mode TEXT NOT NULL DEFAULT 'chat'
                CHECK (mode IN ('chat', 'plan', 'execute', 'team', 'subagent')),
            status TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('queued', 'planning', 'running', 'awaiting_approval',
                    'waiting_input', 'budget_paused', 'interrupted',
                    'completed', 'failed', 'cancelled')),
            title TEXT NOT NULL DEFAULT '',
            user_message_id TEXT,
            assistant_message_id TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            error TEXT,
            metadata JSON,
            request_key TEXT,
            request_hash TEXT,
            request JSON,
            result JSON,
            resumed_from_id TEXT REFERENCES agent_runs(id) ON DELETE SET NULL
        )
    """)
    columns = ("id, session_id, mode, status, title, user_message_id, "
               "assistant_message_id, created_at, started_at, completed_at, error, metadata")
    conn.execute(f"INSERT INTO agent_runs_v31 ({columns}) SELECT {columns} FROM agent_runs")
    conn.execute("DROP TABLE agent_runs")
    conn.execute("ALTER TABLE agent_runs_v31 RENAME TO agent_runs")
    for row in objects:
        conn.execute(row[0])
    conn.execute("""
        CREATE UNIQUE INDEX idx_run_request_key ON agent_runs(session_id, request_key)
        WHERE request_key IS NOT NULL
    """)
