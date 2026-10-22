"""Persist run changes alongside their state, without rebuilding existing tables."""

from __future__ import annotations

from typing import Any

VERSION = 30


def run(conn: Any) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS run_events (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL CHECK (seq > 0),
            type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            data JSON NOT NULL,
            UNIQUE (run_id, seq)
        )
    """)
