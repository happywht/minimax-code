"""Add project-level organization (v0.10.0).

* ``projects`` table — user-defined folders that group sessions.
* ``sessions.project_id`` FK back to ``projects``.
* A reserved ``inbox`` project is seeded automatically; sessions created
  without an explicit project land here, and deleting a project moves
  its sessions back to the inbox rather than dropping them.
"""

from __future__ import annotations

from typing import Any

from ..dao._base import now_iso

VERSION = 16

INBOX_ID = "inbox"
INBOX_NAME = "收件箱"
INBOX_DESCRIPTION = "未归类任务默认目录"

DDL = r"""
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    archived    INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_projects_archived_updated
    ON projects (archived, updated_at DESC);

ALTER TABLE sessions ADD COLUMN project_id TEXT NOT NULL DEFAULT 'inbox' REFERENCES projects(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_sessions_project_archived_updated
    ON sessions (project_id, archived, updated_at DESC);
"""


def run(conn: Any) -> None:
    conn.executescript(DDL)
    now = now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO projects (id, name, description, archived, created_at, updated_at)
        VALUES (?, ?, ?, 0, ?, ?)
        """,
        (INBOX_ID, INBOX_NAME, INBOX_DESCRIPTION, now, now),
    )
    # Existing rows from before this migration were created with the
    # default 'inbox' value, but be explicit in case a previous SQLite
    # version left them NULL.
    conn.execute(
        "UPDATE sessions SET project_id = ? WHERE project_id IS NULL",
        (INBOX_ID,),
    )
