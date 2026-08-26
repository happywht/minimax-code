"""Regression tests for migration 029 — legacy sub-agent budget lift (P0-3).

Field report: rows created under migration 010's ``DEFAULT 8`` were still
sitting at ``max_iterations = 8`` in real databases. The code-level
default (now 100) only rescues *new* rows and ``NULL`` fallbacks — an
explicit 8 in the column wins every time, so sub-agents kept hitting the
budget wall long after the runtime default was raised.

Migration 029 lifts exactly the legacy 8s to 100. It must not touch:
* user-tuned budgets (anything that is not 8),
* re-run behaviour (idempotent — replay changes zero rows).

(The column is ``NOT NULL DEFAULT 8`` — NULL rows cannot exist in the
database; the runtime fallback only covers configs built in code.)

The upgrade-path test builds a genuine pre-029 database by replaying
migrations 001..028 in order, seeds rows the way they looked in the
wild, then applies 029 alone.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from minimax_code.storage.db import Database, make_temp_database_path
from minimax_code.storage.migrations import discover_migrations


def _apply_through(db: Database, upto: int) -> None:
    """Replay migrations 001..upto (inclusive) in version order."""
    runs = dict(discover_migrations(applied=set()))
    conn = db._conn  # type: ignore[attr-defined]
    for version in sorted(runs):
        if version > upto:
            break
        runs[version](conn)


def _budgets(db: Database, name: str) -> int | None:
    row = db.fetchone(
        "SELECT max_iterations FROM agents WHERE name = ?", (name,)
    )
    assert row is not None, f"seeded row {name!r} vanished"
    return row[0]


def test_migration_029_in_registry() -> None:
    versions = {v for v, _run in discover_migrations(applied=[])}
    assert 29 in versions


def test_migration_029_lifts_only_legacy_eight(tmp_path: Path) -> None:
    db = Database(make_temp_database_path(tmp_path))
    try:
        _apply_through(db, 28)
        # Seed rows as they looked in the wild: two legacy 8s (migration
        # 010 era), a reviewer-style 10, a user-tuned 50. No NULL rows —
        # the column is NOT NULL.
        seeds: list[tuple[str, int]] = [
            ("legacy_a", 8),
            ("legacy_b", 8),
            ("reviewer", 10),
            ("tuned", 50),
        ]
        for name, budget in seeds:
            with db.transaction() as conn:
                conn.execute(
                    "INSERT INTO agents (id, name, system_prompt, created_at, "
                    "max_iterations) VALUES (?, ?, '', '2026-01-01T00:00:00Z', ?)",
                    (f"agent_{uuid.uuid4().hex[:12]}", name, budget),
                )

        dict(discover_migrations(applied=set()))[29](db._conn)  # type: ignore[attr-defined]

        assert _budgets(db, "legacy_a") == 100
        assert _budgets(db, "legacy_b") == 100
        assert _budgets(db, "reviewer") == 10
        assert _budgets(db, "tuned") == 50
    finally:
        db.close()


def test_migration_029_replay_is_noop(tmp_path: Path) -> None:
    db = Database(make_temp_database_path(tmp_path))
    try:
        db.migrate()  # full chain — includes 029
        # A user manually setting a row back to 8 (or a legacy restore)
        # is lifted exactly once by a *replay* — this pins that the
        # migration itself is safe to re-run: same predicate, same lift.
        with db.transaction() as conn:
            conn.execute(
                "INSERT INTO agents (id, name, system_prompt, created_at, "
                "max_iterations) VALUES (?, 'replay_eight', '', "
                "'2026-01-01T00:00:00Z', 8)",
                (f"agent_{uuid.uuid4().hex[:12]}",),
            )
        run_029 = dict(discover_migrations(applied=set()))[29]
        conn = db._conn  # type: ignore[attr-defined]
        run_029(conn)
        assert _budgets(db, "replay_eight") == 100
        # Second replay: nothing left at 8, zero rows change.
        run_029(conn)
        assert _budgets(db, "replay_eight") == 100
        total = db.fetchone("SELECT COUNT(*) FROM agents WHERE max_iterations = 8")
        assert total is not None and total[0] == 0
    finally:
        db.close()


def test_migration_029_default_chain_produces_100(tmp_path: Path) -> None:
    """Fresh databases migrate cleanly with 029 in the chain."""
    db = Database(make_temp_database_path(tmp_path))
    try:
        db.migrate()
        applied = {
            row[0]
            for row in db.fetchall("SELECT version FROM schema_migrations")
        }
        assert 29 in applied
    finally:
        db.close()
