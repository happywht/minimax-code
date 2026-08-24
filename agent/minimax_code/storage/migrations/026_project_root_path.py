"""Add ``root_path`` to projects for per-project workspace roots (v1.3.0).

A project's ``root_path`` binds its sessions to a directory on disk: file
tools anchor relative paths to it, codebase indexing shards by it, and
git/terminal/patch handlers resolve against it. Empty string (the default)
keeps the legacy process-wide workspace semantics, so existing databases
behave identically until a user explicitly sets a root.

Idempotent by design: the column is guarded with ``PRAGMA table_info`` so
the migration replays cleanly over a partially-migrated database.
"""

from __future__ import annotations

from typing import Any

VERSION = 26


def run(conn: Any) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "root_path" not in existing:
        conn.execute("ALTER TABLE projects ADD COLUMN root_path TEXT NOT NULL DEFAULT ''")


__all__ = ["VERSION", "run"]
