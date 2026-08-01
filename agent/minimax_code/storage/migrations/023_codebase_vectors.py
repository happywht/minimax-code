"""Vector storage for codebase chunk embeddings using sqlite-vec.

v0.11.0 Milestone 2 — hybrid keyword + vector retrieval.
"""

from __future__ import annotations

from typing import Any

VERSION = 23


DDL = r"""
-- sqlite-vec virtual table storing one dense embedding per chunk.
-- The rowid mirrors codebase_chunks.rowid so joins are simple.
CREATE VIRTUAL TABLE IF NOT EXISTS codebase_chunks_vec USING vec0(
    embedding FLOAT[128]
);
"""


def run(conn: Any) -> None:
    conn.executescript(DDL)


__all__ = ["VERSION", "run"]
