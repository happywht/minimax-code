"""Add workspace/worktree metadata to sessions."""

from __future__ import annotations

from typing import Any

VERSION = 13

DDL = r"""
ALTER TABLE sessions ADD COLUMN workspace_mode TEXT NOT NULL DEFAULT 'local'
    CHECK (workspace_mode IN ('local', 'worktree'));
ALTER TABLE sessions ADD COLUMN workspace_path TEXT;
ALTER TABLE sessions ADD COLUMN worktree_branch TEXT;
ALTER TABLE sessions ADD COLUMN base_branch TEXT;

CREATE INDEX IF NOT EXISTS idx_sessions_workspace_mode_updated
    ON sessions (workspace_mode, updated_at DESC);
"""


def run(conn: Any) -> None:
    conn.executescript(DDL)
