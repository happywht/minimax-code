"""Add ``validate_command`` to projects for the M3 acceptance loop (v1.6.2).

A project may declare the command that proves its code is good (e.g.
``pytest -x -q``). Runs that modify files inside the project run this
command before completing and attach the result to the run's
``verification`` acceptance evidence — a verbal "done" from the model
is not proof (M3: 完成可验收的编程闭环).

Idempotent by design: the column is guarded with ``pragma_table_info``
so the migration replays cleanly over a partially-migrated database.
"""

from __future__ import annotations

from typing import Any

VERSION = 32


def run(conn: Any) -> None:
    existing = {row[1] for row in conn.execute("PRAGMA table_info(projects)").fetchall()}
    if "validate_command" not in existing:
        conn.execute(
            "ALTER TABLE projects ADD COLUMN validate_command TEXT NOT NULL DEFAULT ''"
        )


__all__ = ["VERSION", "run"]
