"""Add the ``hosts`` table for remote Linux run hosts (M5).

A host row registers one Linux machine that can execute tasks while
the Windows desktop is away: SSH coordinates (referenced, never a
password), the last probe result and the agent handshake version.
Deleting a host keeps its runs — they are history, not configuration.
"""

from __future__ import annotations

from typing import Any

VERSION = 33


def run(conn: Any) -> None:
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(hosts)").fetchall()
    }
    if existing:
        return
    conn.execute(
        """
        CREATE TABLE hosts (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            hostname TEXT NOT NULL,
            port INTEGER NOT NULL DEFAULT 22,
            username TEXT NOT NULL,
            identity_file TEXT NOT NULL DEFAULT '',
            enabled INTEGER NOT NULL DEFAULT 1,
            last_probe_at TEXT,
            last_probe_ok INTEGER,
            agent_version TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        "CREATE UNIQUE INDEX idx_hosts_name ON hosts(name)"
    )


__all__ = ["VERSION", "run"]
