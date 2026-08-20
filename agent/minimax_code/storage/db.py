"""SQLite storage layer for MiniMax Code.

This package owns the on-disk database the Python agent uses for
sessions, messages, tasks, skills, scheduled jobs, sub-agent
configuration, permission rules, and paired mobile devices.

Design notes
------------
* **Two connection styles.** The agent itself is asyncio, so the
  preferred access path is :class:`AsyncDatabase` (wraps ``aiosqlite``).
  Synchronous contexts (background threads, scripts, ad-hoc CLI
  tooling) can use :class:`Database` (wraps the stdlib ``sqlite3``).
  Both share a single on-disk file and run the same migration set.

* **Single-writer model.** SQLite serializes writes anyway, but the
  agent is multi-threaded (Tauri spawns a Python sidecar; tools may
  run in worker pools). The sync wrapper uses a process-wide
  ``threading.RLock`` around transactions; the async wrapper relies on
  ``aiosqlite``'s single connection and runs all queries sequentially
  through a queue.

* **Migrations.** A small bootstrapper scans the
  :mod:`minimax_code.storage.migrations` package for ``NNN_*.py``
  modules, executes any whose version is not yet recorded in
  ``schema_migrations``, and wraps each one in a transaction.

* **Path resolution.** The default database location is
  ``%APPDATA%\\MiniMaxCode\\data.db`` on Windows
  (``~/Library/Application Support/MiniMaxCode/data.db`` on macOS,
  ``~/.local/share/MiniMaxCode/data.db`` on Linux). Tests override
  this with a temporary file via :func:`make_temp_database_path`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC
from pathlib import Path

import aiosqlite
import platformdirs

from .migrations import discover_migrations, ensure_migration_table

try:
    import sqlite_vec

    _SQLITE_VEC_AVAILABLE = True
except Exception:  # pragma: no cover — sqlite-vec is a required dep, but be defensive
    sqlite_vec = None  # type: ignore[assignment]
    _SQLITE_VEC_AVAILABLE = False

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

_APP_NAME = "MiniMaxCode"
_APP_AUTHOR = "MiniMax"  # only used for *nix-style data dirs that demand an author
_DB_FILENAME = "data.db"


def default_data_dir() -> Path:
    """Return the OS-appropriate user data directory for the app.

    On Windows this is ``%APPDATA%\\MiniMaxCode`` (roaming), on macOS
    ``~/Library/Application Support/MiniMaxCode``, and on Linux
    ``${XDG_DATA_HOME:-~/.local/share}/MiniMaxCode``. The directory is
    *not* created by this function; callers should use
    :func:`ensure_data_dir` for that.

    Override via the ``MINIMAX_CODE_DATA_DIR`` environment variable —
    used by tests and smoke tests to point at a scratch directory
    without polluting the real user profile.
    """
    override = os.environ.get("MINIMAX_CODE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return Path(
        platformdirs.user_data_dir(
            appname=_APP_NAME,
            appauthor=False,  # spec: %APPDATA%\MiniMaxCode, no author segment
            roaming=True,  # roaming on Windows -> %APPDATA%
        )
    )


def default_database_path() -> Path:
    """Absolute path to the production SQLite database file."""
    return default_data_dir() / _DB_FILENAME


def ensure_data_dir(path: Path | None = None) -> Path:
    """Create the data directory (and parents) if it doesn't exist.

    Returns the resolved directory. Safe to call repeatedly.
    """
    target = (path or default_data_dir()).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    return target


def make_temp_database_path(tmp_dir: Path | None = None) -> Path:
    """Build a fresh path under the OS temp dir for tests / scratch use.

    The filename embeds PID, time-ns, and a uuid4 fragment so two
    calls in quick succession on the same process / tempdir are
    guaranteed to produce distinct paths.
    """
    import tempfile
    import time
    import uuid

    base = Path(tmp_dir) if tmp_dir is not None else Path(tempfile.gettempdir())
    base.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex[:8]
    return base / (
        f"minimax-code-{os.getpid()}-{time.time_ns()}-{suffix}-{_DB_FILENAME}"
    )


# ---------------------------------------------------------------------------
# Shared pragmas / connection configuration
# ---------------------------------------------------------------------------

#: SQLite pragmas applied to every connection we open. ``WAL`` gives
#: us concurrent readers + a single writer (matches our async model);
#: ``foreign_keys`` enforces referential integrity (off by default in
#: SQLite); ``busy_timeout`` keeps writes from failing on transient
#: locks; ``synchronous=NORMAL`` is the WAL-friendly default.
_DEFAULT_PRAGMAS: tuple[tuple[str, str], ...] = (
    ("journal_mode", "WAL"),
    ("foreign_keys", "ON"),
    ("synchronous", "NORMAL"),
    ("busy_timeout", "5000"),
    ("temp_store", "MEMORY"),
)


def _apply_pragmas_sync(conn: sqlite3.Connection) -> None:
    """Apply the default pragmas to a stdlib ``sqlite3.Connection``."""
    conn.row_factory = sqlite3.Row
    for name, value in _DEFAULT_PRAGMAS:
        conn.execute(f"PRAGMA {name}={value}")


def _load_sqlite_extensions_sync(conn: sqlite3.Connection) -> None:
    """Load optional SQLite extensions (currently sqlite-vec)."""
    if not _SQLITE_VEC_AVAILABLE:
        return
    try:
        conn.enable_load_extension(True)
        conn.load_extension(sqlite_vec.loadable_path())  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 — fail-open; vector tables simply won't work
        logger.debug("failed to load sqlite-vec extension", exc_info=True)


async def _load_sqlite_extensions_async(conn: aiosqlite.Connection) -> None:
    """Load optional SQLite extensions on an aiosqlite connection."""
    if not _SQLITE_VEC_AVAILABLE:
        return
    try:
        await conn.enable_load_extension(True)
        await conn.load_extension(sqlite_vec.loadable_path())  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001 — fail-open
        logger.debug("failed to load sqlite-vec extension", exc_info=True)


def _set_sqlite_udf_safe() -> None:
    """Register Python UDFs that are useful for DAO helpers.

    Currently a no-op (kept as a hook for future ``REGEXP`` /
    ``UUID`` helpers). Lives here so that sync and async wrappers
    converge on the same set of registered functions.
    """


# ---------------------------------------------------------------------------
# Synchronous wrapper
# ---------------------------------------------------------------------------


class Database:
    """Thread-safe synchronous wrapper around a single ``sqlite3.Connection``.

    A single connection is opened per :class:`Database` instance and
    guarded by an :class:`threading.RLock`. This matches SQLite's own
    serialized writer model and is the recommended pattern for the
    background scheduler / CLI tools that may need to read state
    while the asyncio agent loop is also active.
    """

    def __init__(self, path: Path | str, *, pragmas: bool = True) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            str(self.path),
            detect_types=sqlite3.PARSE_DECLTYPES,
            check_same_thread=False,
            isolation_level=None,  # we manage transactions explicitly
        )
        if pragmas:
            _apply_pragmas_sync(self._conn)
        _load_sqlite_extensions_sync(self._conn)
        _set_sqlite_udf_safe()
        logger.debug("opened sync database at %s", self.path)

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None  # type: ignore[assignment]

    def __enter__(self) -> Database:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # -- migrations ---------------------------------------------------------

    def migrate(self) -> list[int]:
        """Run any pending migrations synchronously.

        Returns the list of newly-applied versions.
        """
        with self._lock:
            ensure_migration_table(self._conn)
            applied = self._applied_versions()
            pending = discover_migrations(applied)
            new_versions: list[int] = []
            for version, run in pending:
                logger.info("applying migration %03d", version)
                # Explicit BEGIN IMMEDIATE (not ``with self._conn:``): the
                # connection runs in autocommit mode (isolation_level=None),
                # where the context manager never opens a transaction and its
                # commit is a no-op — a half-failing migration would leave
                # partial DDL committed on disk. Mirrors transaction().
                try:
                    self._conn.execute("BEGIN IMMEDIATE")
                    run(self._conn)
                    self._conn.execute(
                        "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                        (version, _now_iso()),
                    )
                    self._conn.execute("COMMIT")
                except BaseException:
                    try:
                        self._conn.execute("ROLLBACK")
                    except sqlite3.OperationalError:
                        pass
                    raise
                new_versions.append(version)
            if new_versions:
                logger.info("applied %d migration(s): %s", len(new_versions), new_versions)
            return new_versions

    def _applied_versions(self) -> set[int]:
        rows = self._conn.execute("SELECT version FROM schema_migrations").fetchall()
        return {int(r[0]) for r in rows}

    def applied_versions(self) -> set[int]:
        """Public accessor — used by tests."""
        with self._lock:
            return self._applied_versions()

    # -- transaction helpers ------------------------------------------------

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Context manager that brackets a single transaction.

        Usage::

            with db.transaction() as conn:
                conn.execute(...)

        Commits on clean exit, rolls back on exception.
        """
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.execute("COMMIT")
            except BaseException:
                try:
                    self._conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, seq_of_params) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executemany(sql, seq_of_params)

    def fetchone(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    def fetchall(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    @property
    def raw_connection(self) -> sqlite3.Connection:
        """Escape hatch — the underlying connection (lock not held).

        Use only when you know what you're doing (e.g. inside
        :meth:`transaction`).
        """
        return self._conn


# ---------------------------------------------------------------------------
# Asynchronous wrapper
# ---------------------------------------------------------------------------


class AsyncDatabase:
    """Async wrapper around a single ``aiosqlite.Connection``.

    A single connection is opened per instance. ``aiosqlite`` already
    serializes queries through a worker thread, so we don't need an
    extra lock; we just expose the typical ``execute / fetchone /
    fetchall`` helpers and a transaction context manager that maps
    onto SQLite's ``BEGIN``/``COMMIT``.
    """

    def __init__(self, path: Path | str, *, pragmas: bool = True) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._pragmas = pragmas
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()
        logger.debug("async database will live at %s", self.path)

    # -- lifecycle ----------------------------------------------------------

    async def connect(self) -> None:
        if self._conn is not None:
            return
        # isolation_level=None matches the sync wrapper: we manage
        # transactions explicitly (BEGIN IMMEDIATE via transaction()).
        # The sqlite3 legacy implicit-transaction mode would leave DML
        # issued through bare execute() uncommitted — silently lost on
        # close — and make a later BEGIN fail with "cannot start a
        # transaction within a transaction".
        self._conn = await aiosqlite.connect(str(self.path), isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        if self._pragmas:
            for name, value in _DEFAULT_PRAGMAS:
                await self._conn.execute(f"PRAGMA {name}={value}")
            await self._conn.commit()
        await _load_sqlite_extensions_async(self._conn)
        _set_sqlite_udf_safe()
        logger.debug("opened async database at %s", self.path)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def __aenter__(self) -> AsyncDatabase:
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # -- migrations ---------------------------------------------------------

    async def migrate(self) -> list[int]:
        """Apply any pending migrations; return newly-applied versions.

        Implementation note: ``aiosqlite`` proxies every method call
        through a worker thread, so the sync migration ``run(conn)``
        function can't be called against the async connection
        directly (its ``executescript`` is a coroutine). Instead we
        open a short-lived *sync* :class:`Database` against the same
        file. SQLite's WAL mode makes the schema visible to the
        async connection immediately after the sync ``COMMIT``.
        """
        await self.connect()
        # Open the sync side on the same file. Pragmas match the
        # async side (WAL, foreign_keys, …) so the lock dance works.
        sync = Database(self.path)
        try:
            return sync.migrate()
        finally:
            sync.close()

    async def applied_versions(self) -> set[int]:
        await self.connect()
        assert self._conn is not None
        async with self._conn.execute("SELECT version FROM schema_migrations") as cur:
            rows = await cur.fetchall()
        return {int(r[0]) for r in rows}

    # -- transaction helpers ------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        """Async context manager wrapping a single transaction."""
        await self.connect()
        assert self._conn is not None
        async with self._lock:
            try:
                await self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                await self._conn.commit()
            except BaseException:
                try:
                    await self._conn.rollback()
                except Exception:  # pragma: no cover
                    pass
                raise

    async def execute(self, sql: str, params: tuple | dict = ()) -> aiosqlite.Cursor:
        await self.connect()
        assert self._conn is not None
        async with self._lock:
            return await self._conn.execute(sql, params)

    async def executemany(self, sql: str, seq_of_params) -> aiosqlite.Cursor:
        await self.connect()
        assert self._conn is not None
        async with self._lock:
            return await self._conn.executemany(sql, seq_of_params)

    async def fetchone(self, sql: str, params: tuple | dict = ()) -> aiosqlite.Row | None:
        await self.connect()
        assert self._conn is not None
        async with self._lock:
            async with self._conn.execute(sql, params) as cur:
                return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple | dict = ()) -> list[aiosqlite.Row]:
        await self.connect()
        assert self._conn is not None
        async with self._lock:
            async with self._conn.execute(sql, params) as cur:
                return list(await cur.fetchall())


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """UTC ISO-8601 timestamp, second precision (no microseconds)."""
    from datetime import datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


__all__ = [
    "AsyncDatabase",
    "Database",
    "default_data_dir",
    "default_database_path",
    "ensure_data_dir",
    "make_temp_database_path",
]
