"""Model preferences — single-row table holding the user's chosen model.

Phase 2 introduces the ``ModelSelector`` UI (see
``web/src/components/ModelSelector.tsx``) that needs to
(a) list the candidate models the sidecar supports and
(b) remember which one the user last picked so the next session
    boots up on the same model.

We store the selection in its own one-row table (``id = 1``) rather
than overloading the ``sessions`` or ``agents`` tables because the
choice is **user-global**, not per-session or per-agent. A separate
table keeps the schema honest and makes the persistence boundary
obvious for future migrations (e.g. "default model per agent",
"model aliases").

The row is seeded with ``MiniMax-M3`` (the package's
``agent.llm.DEFAULT_MODEL``) so a fresh install has a sensible
default without the IPC layer having to special-case a missing row.
"""

from __future__ import annotations

from typing import Any

from . import run_script

VERSION = 2


DDL = r"""
-- ---------------------------------------------------------------------------
-- model_prefs (user-global model choice)
-- ---------------------------------------------------------------------------
-- Single-row table: ``id = 1`` is the canonical row, enforced by a
-- CHECK constraint so accidental multi-row inserts are caught at
-- write time. ``updated_at`` is bumped on every set_current so the
-- UI can display "last changed at" if it wants to.
CREATE TABLE IF NOT EXISTS model_prefs (
    id            INTEGER PRIMARY KEY CHECK (id = 1),
    current_model TEXT NOT NULL DEFAULT 'MiniMax-M3',
    updated_at    TEXT NOT NULL
);

-- Idempotent seed — INSERT OR IGNORE means re-running the migration
-- on an already-migrated DB is a no-op. The fixed timestamp keeps
-- the row stable across re-migrations on the same codebase.
INSERT OR IGNORE INTO model_prefs (id, current_model, updated_at)
    VALUES (1, 'MiniMax-M3', '2026-06-02T00:00:00Z');
"""


def run(conn: Any) -> None:
    """Apply the model_prefs migration to ``conn``.

    ``conn`` may be a stdlib ``sqlite3.Connection`` or an
    ``aiosqlite.Connection`` — :func:`run_script` executes the DDL
    one statement at a time on either.
    """
    run_script(conn, DDL)


__all__ = ["DDL", "VERSION", "run"]
