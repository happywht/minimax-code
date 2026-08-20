"""Storage migrations — versioned schema evolution.

Migrations are simple modules named ``NNN_short_name.py`` (e.g.
``001_initial.py``) that expose a ``VERSION`` (int) and a ``run(conn)``
function. The ``run`` function receives a ``sqlite3.Connection`` (or
``aiosqlite.Connection`` — the surface area we use is identical) and
is expected to execute DDL using :func:`run_script` or ``execute``.
``Connection.executescript`` must NOT be used: it issues an implicit
COMMIT that breaks out of the explicit transaction Database.migrate
wraps around each migration.

The :func:`discover_migrations` helper returns a deterministic,
version-sorted list of ``(version, run)`` tuples for the migrations
that have *not* yet been applied.
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
import re
import sqlite3
from collections.abc import Callable, Iterable, Iterator

logger = logging.getLogger(__name__)

#: DDL for the bookkeeping table. Idempotent — safe to run on a fresh
#: database or one that's already been migrated.
MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version    INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL
)
"""

#: Type alias for a migration's run function.
MigrationRun = Callable[[object], None]

# Matches "001_initial.py" — version prefix must be 3+ digits to
# keep migrations sort the same way both numerically and lexically.
_MIGRATION_NAME_RE = re.compile(r"^(\d{3,})_[a-zA-Z0-9_]+$")


def ensure_migration_table(conn) -> None:  # type: ignore[no-untyped-def]
    """Create the ``schema_migrations`` table if it doesn't exist."""
    conn.execute(MIGRATIONS_DDL)


def iter_statements(script: str) -> Iterator[str]:
    """Split a SQL script into complete statements using sqlite3's parser.

    ``sqlite3.complete_statement`` only reports a complete statement once its
    terminating ``;`` is reached *and* it understands trigger bodies, so
    semicolons inside ``CREATE TRIGGER … BEGIN … END;`` don't split early.
    Statements may span multiple lines; blank lines are skipped.
    """
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if buffer.strip() and sqlite3.complete_statement(buffer):
            yield buffer
            buffer = ""
    if buffer.strip():
        yield buffer  # trailing statement without a terminating ';'


def run_script(conn, script: str) -> None:  # type: ignore[no-untyped-def]
    """Execute a multi-statement DDL script one statement at a time.

    Unlike ``Connection.executescript`` this never issues an implicit COMMIT,
    so it stays inside the caller's explicit transaction — a half-failing
    migration rolls back cleanly (see ``Database.migrate``).
    """
    for stmt in iter_statements(script):
        conn.execute(stmt)


def _iter_migration_modules():  # type: ignore[no-untyped-def]
    """Yield ``(version, module)`` for every migration in this package.

    We do this by walking the package directory with :mod:`pkgutil`
    and importing each module that matches the ``NNN_name.py`` shape.
    Modules that don't expose ``VERSION`` / ``run`` are skipped with a
    warning.
    """
    this_pkg = importlib.import_module(__name__)
    for module_info in pkgutil.iter_modules(this_pkg.__path__):  # type: ignore[attr-defined]
        name = module_info.name
        match = _MIGRATION_NAME_RE.match(name)
        if match is None:
            continue
        version = int(match.group(1))
        full_name = f"{this_pkg.__name__}.{name}"
        module = importlib.import_module(full_name)
        run = getattr(module, "run", None)
        declared_version = getattr(module, "VERSION", None)
        if run is None or declared_version is None:
            logger.warning("migration %s missing VERSION/run — skipping", name)
            continue
        if declared_version != version:
            raise RuntimeError(
                f"migration {name} declares VERSION={declared_version} "
                f"but filename implies {version}"
            )
        yield version, run, name


def discover_migrations(applied: Iterable[int]) -> list[tuple[int, MigrationRun]]:
    """Return a version-sorted list of migrations not yet in ``applied``.

    The result is *always* sorted by ascending version so that
    :class:`Database.migrate` applies them in the right order.
    """
    applied_set = set(applied)
    out: list[tuple[int, MigrationRun]] = []
    for version, run, _name in sorted(_iter_migration_modules(), key=lambda x: x[0]):
        if version in applied_set:
            continue
        out.append((version, run))
    return out


__all__ = [
    "MIGRATIONS_DDL",
    "MigrationRun",
    "discover_migrations",
    "ensure_migration_table",
    "iter_statements",
    "run_script",
]
