"""Add the ``root`` dimension to codebase index tables (v1.3.0).

Historically the codebase index was a single global shard: every
project's chunks landed in the same ``codebase_chunks`` rows keyed only
by ``file_path``. With per-project workspace roots, two projects holding
the same relative path (``src/main.py``) would overwrite each other, and
an incremental build of project A would treat project B's files as
"deleted" and wipe them.

This migration shards the index by root:

1. ``codebase_chunks`` gains ``root TEXT NOT NULL DEFAULT ''`` — legacy
   rows keep the empty-string key, so the default-root index survives
   with zero re-indexing. The FTS5 triggers (external-content, keyed by
   rowid) are untouched by the new column.
2. ``codebase_file_meta`` is rebuilt with a ``(root, file_path)``
   composite primary key using the standard four-step table rebuild
   (create ``_new`` → INSERT SELECT → DROP → RENAME), which is what makes
   incremental diffing per-root correct.

Both steps probe before applying, so the migration replays cleanly over
a partially-migrated database. The vec0 virtual table maps rowid →
embedding and needs no schema change; root filtering happens when
rowids are joined back to ``codebase_chunks``.
"""

from __future__ import annotations

from typing import Any

VERSION = 27


def run(conn: Any) -> None:
    # Step 1 — chunks table: additive ALTER, guarded by PRAGMA.
    chunk_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(codebase_chunks)").fetchall()
    }
    if "root" not in chunk_cols:
        conn.execute(
            "ALTER TABLE codebase_chunks ADD COLUMN root TEXT NOT NULL DEFAULT ''"
        )

    # Step 2 — file meta: rebuild with a (root, file_path) composite PK.
    meta_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(codebase_file_meta)").fetchall()
    }
    if "root" in meta_cols:
        return

    # Clear any leftover from a previous failed run of this migration.
    conn.execute("DROP TABLE IF EXISTS codebase_file_meta_new")
    conn.execute(
        """
        CREATE TABLE codebase_file_meta_new (
            root        TEXT NOT NULL DEFAULT '',
            file_path   TEXT NOT NULL,
            mtime       REAL NOT NULL,
            size        INTEGER NOT NULL,
            indexed_at  TEXT NOT NULL,
            PRIMARY KEY (root, file_path)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO codebase_file_meta_new (root, file_path, mtime, size, indexed_at)
        SELECT '', file_path, mtime, size, indexed_at FROM codebase_file_meta
        """
    )
    conn.execute("DROP TABLE codebase_file_meta")
    conn.execute("ALTER TABLE codebase_file_meta_new RENAME TO codebase_file_meta")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_codebase_file_meta_path "
        "ON codebase_file_meta (file_path)"
    )


__all__ = ["VERSION", "run"]
