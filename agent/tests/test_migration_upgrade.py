"""Regression tests for the migration upgrade path (v0.11.0 hardening).

Covers the production failure modes found while auditing a real user
database stuck at schema 15:

1. **Clean upgrade** — a database at schema 15 with live session rows
   migrates to head in one ``migrate()`` call, backfilling
   ``sessions.project_id`` to the inbox project.
2. **Dirty self-heal** — a database whose 016 run half-failed under the
   old executescript-based runner (projects table committed, sessions
   never got ``project_id``, versions 16-24 unrecorded while the 17-24
   DDL had already landed) converges to the same final state on the
   next startup.
3. **No-regression guard** — migration ``run`` functions must never
   call ``executescript``: its implicit COMMIT breaks out of the
   explicit transaction ``Database.migrate`` wraps around each step.
4. **Version pins agree** — ``pyproject.toml``,
   ``minimax_code.__version__`` and the fallback in ``version.py``
   must all match so a release bump can't drift.
"""

from __future__ import annotations

import importlib
import inspect
import tomllib
from collections.abc import Iterator
from pathlib import Path

import pytest

from minimax_code.storage.db import Database, make_temp_database_path
from minimax_code.storage.migrations import (
    MigrationRun,
    discover_migrations,
    ensure_migration_table,
)

#: Every version the migrations package currently ships.
ALL_VERSIONS = {version for version, _run in discover_migrations(applied=[])}

#: Migration 016's own DDL constants — reused to fabricate the dirty
#: state exactly the way the old runner left it behind.
_M16 = importlib.import_module(
    "minimax_code.storage.migrations.016_projects_and_session_project_id"
)

_T0 = "2026-08-20T00:00:00Z"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Iterator[Path]:
    """Fresh temp database path for each test."""
    return make_temp_database_path(tmp_path)


def _migrations_up_to(limit: int) -> list[tuple[int, MigrationRun]]:
    """All shipped migrations with version <= ``limit``, in apply order."""
    return [(v, run) for v, run in discover_migrations(applied=[]) if v <= limit]


def _apply(db: Database, pairs: list[tuple[int, MigrationRun]], *, record: bool) -> None:
    """Run migration pairs directly, optionally recording their versions.

    ``record=False`` replays the old executescript failure signature:
    DDL landed on disk, ``schema_migrations`` never updated.
    """
    ensure_migration_table(db.raw_connection)
    for version, run in pairs:
        run(db.raw_connection)
        if record:
            db.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, _T0),
            )


def _seed_sessions(db: Database, count: int) -> None:
    """Insert live session rows against the pre-016 schema."""
    for i in range(count):
        db.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at)"
            " VALUES (?, ?, ?, ?)",
            (f"sess_{i}", f"session {i}", _T0, _T0),
        )


def _assert_fully_migrated(db: Database, *, session_count: int) -> None:
    """Assert the database converged to the healthy head state."""
    # Every shipped version recorded — including 16-24 that the dirty
    # database was missing.
    assert db.applied_versions() == ALL_VERSIONS

    # Live rows survived the upgrade, all backfilled to the inbox project.
    assert db.fetchone("SELECT COUNT(*) FROM sessions")[0] == session_count
    assert (
        db.fetchone("SELECT COUNT(*) FROM sessions WHERE project_id = 'inbox'")[0]
        == session_count
    )
    assert db.fetchone("SELECT COUNT(*) FROM projects WHERE id = 'inbox'")[0] == 1

    # No dangling foreign keys anywhere.
    assert db.fetchall("PRAGMA foreign_key_check") == []

    # v0.11.0 objects exist: memories FTS triggers + both virtual tables.
    names = {row[0] for row in db.fetchall("SELECT name FROM sqlite_master")}
    assert "trg_memories_fts_insert" in names
    assert "codebase_chunks_fts" in names
    assert "codebase_chunks_vec" in names


# ---------------------------------------------------------------------------
# 1. Clean upgrade
# ---------------------------------------------------------------------------


def test_clean_upgrade_from_v15_with_live_sessions(db_path: Path) -> None:
    db = Database(db_path)
    try:
        _apply(db, _migrations_up_to(15), record=True)
        _seed_sessions(db, 5)

        db.migrate()

        _assert_fully_migrated(db, session_count=5)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. Dirty self-heal (the real-world stuck database)
# ---------------------------------------------------------------------------


def test_dirty_database_self_heals_on_next_migrate(db_path: Path) -> None:
    db = Database(db_path)
    try:
        _apply(db, _migrations_up_to(15), record=True)
        _seed_sessions(db, 5)

        # Replay the corruption the old executescript-based runner left
        # behind: 016's leading DDL committed (projects table + index)
        # before the ALTER failed, and migrations 17-24 had applied
        # without their versions ever being recorded.
        db.execute(_M16._CREATE_PROJECTS)
        db.execute(_M16._CREATE_PROJECTS_INDEX)
        _apply(
            db,
            [(v, run) for v, run in discover_migrations(applied=[]) if 17 <= v <= 24],
            record=False,
        )

        # Sanity: the dirty state really is what we claim to heal —
        # versions 16-24 unrecorded and sessions still lacks project_id.
        assert 16 not in db.applied_versions()
        columns = {row[1] for row in db.execute("PRAGMA table_info(sessions)").fetchall()}
        assert "project_id" not in columns

        db.migrate()

        _assert_fully_migrated(db, session_count=5)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 3. No-regression guard
# ---------------------------------------------------------------------------


def test_migration_run_functions_never_call_executescript() -> None:
    """``executescript`` implicitly COMMITs and would break migrate()'s
    per-migration transaction — a leftover call makes a later failure
    land half-committed DDL on disk."""
    for version, run in discover_migrations(applied=[]):
        source = inspect.getsource(run)
        assert "executescript" not in source, f"migration {version:03d} uses executescript"


# ---------------------------------------------------------------------------
# 4. Version pins agree
# ---------------------------------------------------------------------------


def test_version_pins_agree_across_project_files() -> None:
    pyproject = Path(__file__).resolve().parents[1] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))

    from minimax_code import __version__
    from minimax_code.version import _FALLBACK_VERSION

    assert data["project"]["version"] == __version__ == _FALLBACK_VERSION
