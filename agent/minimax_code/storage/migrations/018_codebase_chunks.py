"""Migration 018 — codebase_chunks + FTS5 index (v0.11.0 Milestone 2)."""

from __future__ import annotations

VERSION: int = 18

DDL = r"""
CREATE TABLE IF NOT EXISTS codebase_chunks (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    start_line INTEGER NOT NULL DEFAULT 1,
    end_line INTEGER NOT NULL DEFAULT 1,
    content TEXT NOT NULL,
    metadata TEXT,              -- JSON: language, symbols, etc.
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_codebase_chunks_file_path
    ON codebase_chunks (file_path);

CREATE INDEX IF NOT EXISTS idx_codebase_chunks_updated_at
    ON codebase_chunks (updated_at DESC);

-- FTS5 virtual table for keyword search over file path + content.
CREATE VIRTUAL TABLE IF NOT EXISTS codebase_chunks_fts USING fts5(
    file_path,
    content,
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- Keep the FTS index in sync with the main table.
CREATE TRIGGER IF NOT EXISTS trg_codebase_chunks_fts_insert
AFTER INSERT ON codebase_chunks
BEGIN
    INSERT INTO codebase_chunks_fts (rowid, file_path, content)
    VALUES (new.rowid, new.file_path, new.content);
END;

CREATE TRIGGER IF NOT EXISTS trg_codebase_chunks_fts_update
AFTER UPDATE ON codebase_chunks
BEGIN
    UPDATE codebase_chunks_fts
    SET file_path = new.file_path,
        content = new.content
    WHERE rowid = new.rowid;
END;

CREATE TRIGGER IF NOT EXISTS trg_codebase_chunks_fts_delete
AFTER DELETE ON codebase_chunks
BEGIN
    DELETE FROM codebase_chunks_fts WHERE rowid = old.rowid;
END;
"""


def run(conn) -> None:
    conn.executescript(DDL)
