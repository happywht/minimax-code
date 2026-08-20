"""Migration 019 — memories table + FTS5 index (v0.11.0 Milestone 3)."""

from __future__ import annotations

from . import run_script

VERSION: int = 19

DDL = r"""
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    session_id TEXT,
    content TEXT NOT NULL,
    category TEXT,              -- 'preference' | 'decision' | 'lesson' | 'fact'
    confidence REAL,            -- 0..1
    source TEXT,                -- optional human-readable source
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_memories_project_category
    ON memories (project_id, category);

CREATE INDEX IF NOT EXISTS idx_memories_session_id
    ON memories (session_id);

CREATE INDEX IF NOT EXISTS idx_memories_updated_at
    ON memories (updated_at DESC);

-- FTS5 virtual table for full-text search over memory content.
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
    content,
    content_rowid='rowid',
    tokenize='porter unicode61'
);

-- Keep the FTS index in sync with the main table.
CREATE TRIGGER IF NOT EXISTS trg_memories_fts_insert
AFTER INSERT ON memories
BEGIN
    INSERT INTO memories_fts (rowid, content)
    VALUES (new.rowid, new.content);
END;

CREATE TRIGGER IF NOT EXISTS trg_memories_fts_update
AFTER UPDATE ON memories
BEGIN
    UPDATE memories_fts
    SET content = new.content
    WHERE rowid = new.rowid;
END;

CREATE TRIGGER IF NOT EXISTS trg_memories_fts_delete
AFTER DELETE ON memories
BEGIN
    DELETE FROM memories_fts WHERE rowid = old.rowid;
END;
"""


def run(conn) -> None:  # type: ignore[no-untyped-def]
    run_script(conn, DDL)
