"""Add the ``session_checkpoints`` table (R310, workspace snapshot layer).

Each row is one workspace checkpoint: a git-stash ref capturing tracked
working-tree changes plus an optional on-disk copy of untracked files,
so a session can be rewound to a known-good state after a failed edit
or an agent misstep. This is the persistence half of the R13-R14
checkpoint capability — the runtime lives in
:mod:`minimax_code.workspace.checkpoint`.

Design notes
------------
* **No foreign key to ``sessions``.** Like ``notifications``, the link
  is logical only — a checkpoint survives even if the session row is
  pruned, and DAO callers delete by ``session_id`` explicitly. This
  keeps the table insert-failure-free during partial session teardown.
* **JSON columns for file lists.** ``tracked_files`` / ``untracked_files``
  are JSON arrays of repo-relative paths. SQLite has no native array
  type; JSON-in-TEXT is the established pattern in this schema.
* ``has_untracked_snapshot`` is a 0/1 flag — whether the on-disk file
  copy under ``<data_dir>/checkpoints/<id>/`` exists and should be
  restored on rewind.
"""

from __future__ import annotations

from typing import Any

from . import run_script

VERSION = 15

DDL = r"""
CREATE TABLE IF NOT EXISTS session_checkpoints (
    id                      TEXT PRIMARY KEY,
    session_id              TEXT NOT NULL,
    label                   TEXT NOT NULL,
    message                 TEXT NOT NULL DEFAULT '',
    git_stash_ref           TEXT,
    branch                  TEXT,
    tracked_files           TEXT NOT NULL DEFAULT '[]',
    untracked_files         TEXT NOT NULL DEFAULT '[]',
    has_untracked_snapshot  INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_session_checkpoints_session_created
    ON session_checkpoints (session_id, created_at DESC);
"""


def run(conn: Any) -> None:
    run_script(conn, DDL)
