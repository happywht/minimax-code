"""Add a ``result`` column to the ``tasks`` table.

The scheduler's ``_fire`` computed a per-run result string but had
nowhere to persist it — the text only ever reached the logger. v1.2.0
stores it on the task row so the ledger (and the ``task.*`` IPC
surface, which hydrates via ``SELECT *``) can show what a cron job
actually produced, e.g. the LLM reply for ``{"prompt": ...}`` payloads.
"""

from __future__ import annotations

from typing import Any

VERSION = 25


def run(conn: Any) -> None:
    """Add ``tasks.result`` if the column is not present yet."""
    existing = {
        row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
    }
    if "result" not in existing:
        conn.execute("ALTER TABLE tasks ADD COLUMN result TEXT")


__all__ = ["VERSION", "run"]
