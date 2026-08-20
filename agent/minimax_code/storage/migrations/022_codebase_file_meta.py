"""Track per-file index state to enable incremental codebase indexing.

v0.11.0 Milestone 2 — incremental rebuilds avoid re-parsing unchanged files.
"""

from __future__ import annotations

from typing import Any

from . import run_script

VERSION = 22


DDL = r"""
CREATE TABLE IF NOT EXISTS codebase_file_meta (
    file_path   TEXT PRIMARY KEY,
    mtime       REAL NOT NULL,
    size        INTEGER NOT NULL,
    indexed_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_codebase_file_meta_path
    ON codebase_file_meta (file_path);
"""


def run(conn: Any) -> None:
    run_script(conn, DDL)


__all__ = ["VERSION", "run"]
