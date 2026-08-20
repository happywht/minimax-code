"""Add project-level organization (v0.10.0).

* ``projects`` table — user-defined folders that group sessions.
* ``sessions.project_id`` — logical grouping key, backfilled to ``inbox``.
* A reserved ``inbox`` project is seeded automatically; sessions created
  without an explicit project land here, and deleting a project moves
  its sessions back to the inbox rather than dropping them.

Idempotent by design (v0.11.0 hardening): every statement is guarded so the
migration replays cleanly over a database left half-migrated by an earlier
failed run (e.g. ``projects`` created but ``sessions.project_id`` missing).
"""

from __future__ import annotations

from typing import Any

from ..dao._base import now_iso

VERSION = 16

INBOX_ID = "inbox"
INBOX_NAME = "收件箱"
INBOX_DESCRIPTION = "未归类任务默认目录"

_CREATE_PROJECTS = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    archived    INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
)
"""

_CREATE_PROJECTS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_projects_archived_updated
    ON projects (archived, updated_at DESC)
"""

# No REFERENCES clause on purpose:
# * SQLite rejects ``ADD COLUMN ... REFERENCES ... <non-NULL DEFAULT>`` whenever
#   foreign_keys=ON and the table already has rows, which is exactly the
#   upgrade path this migration must survive.
# * ``NOT NULL DEFAULT 'inbox'`` and ``ON DELETE SET NULL`` are mutually
#   exclusive anyway — the declared FK action could never fire.
# Re-parenting on project delete is done explicitly by ProjectsDAO.delete
# (UPDATE sessions SET project_id='inbox' ...), the only projects delete path.
_ADD_PROJECT_ID = (
    "ALTER TABLE sessions ADD COLUMN project_id TEXT NOT NULL DEFAULT 'inbox'"
)

_CREATE_SESSIONS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_sessions_project_archived_updated
    ON sessions (project_id, archived, updated_at DESC)
"""


def run(conn: Any) -> None:
    conn.execute(_CREATE_PROJECTS)
    conn.execute(_CREATE_PROJECTS_INDEX)

    existing = {row[1] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()}
    if "project_id" not in existing:
        conn.execute(_ADD_PROJECT_ID)

    conn.execute(_CREATE_SESSIONS_INDEX)

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


__all__ = ["VERSION", "run", "INBOX_ID"]
