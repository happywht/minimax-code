"""IPC handlers — ``data.*`` namespace (data portability, M4 / R21+).

``data.export``
    Dump every business table into a single JSON document (the RPC
    result itself); the frontend turns it into a downloaded file.
    The payload is self-describing: ``format`` / ``schema_version`` /
    ``app_version`` / ``exported_at`` headers plus per-table row lists.

``data.import`` (R22)
    Validate and replace-import such an envelope inside a single
    transaction — truncate each envelope table, then refill it from
    the envelope rows. Idempotent: importing the same file twice
    leaves the database in the same state as importing it once.
"""

from __future__ import annotations

import heapq
import logging
import os
import sqlite3
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .handler_utils import HandlerError
from .protocol import INTERNAL_ERROR, INVALID_PARAMS, DataBackupListParams
from .server import Context

logger = logging.getLogger(__name__)

#: Stable discriminator for the export envelope.
EXPORT_FORMAT = "minimax-code-export"

#: Tables that hold migration bookkeeping, not user data. The applied
#: version is captured once in the top-level ``schema_version`` field.
_EXCLUDED_TABLES = frozenset({"schema_migrations"})


def _is_virtual_table(sql: str | None) -> bool:
    """True for FTS / vec shadow virtual tables.

    Virtual tables are derived state — the FTS index is kept in sync by
    triggers on ``memories`` and the vec index is rebuilt by indexing —
    so they are excluded from the dump and recreated on the target.
    """
    return bool(sql) and sql.lstrip().upper().startswith("CREATE VIRTUAL TABLE")


async def dump_database(db: Any) -> dict[str, Any]:
    """Dump every business table of *db* into an export envelope.

    Table set is discovered from ``sqlite_master`` (not hardcoded), so
    future migrations that add tables are picked up automatically.
    Rows are serialised as plain dicts of column name -> value.
    """
    schema_rows = await db.fetchall(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    tables: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    for row in schema_rows:
        name = row["name"]
        sql = row["sql"]
        if name in _EXCLUDED_TABLES or _is_virtual_table(sql):
            continue
        # ``name`` comes from sqlite_master, never user input — the
        # f-string below cannot be an injection vector.
        table_rows = await db.fetchall(f'SELECT * FROM "{name}"')  # noqa: S608
        tables[name] = [dict(r) for r in table_rows]
        counts[name] = len(table_rows)

    versions = await db.applied_versions()
    from .. import __version__

    return {
        "format": EXPORT_FORMAT,
        "schema_version": max(versions) if versions else 0,
        "app_version": __version__,
        "exported_at": datetime.now(UTC).isoformat(),
        "counts": counts,
        "tables": tables,
    }


# ---------------------------------------------------------------------------
# Import (R22)
# ---------------------------------------------------------------------------


def _validate_envelope(envelope: Any, current_schema_version: int) -> None:
    """Structural + compatibility validation; raises ValueError on bad input."""
    if not isinstance(envelope, dict):
        raise ValueError("envelope must be an object")
    if envelope.get("format") != EXPORT_FORMAT:
        raise ValueError(f"envelope.format must be {EXPORT_FORMAT!r}")
    tables = envelope.get("tables")
    if not isinstance(tables, dict) or not all(
        isinstance(rows, list) and all(isinstance(r, dict) for r in rows)
        for rows in tables.values()
    ):
        raise ValueError("envelope.tables must map table name -> list of row objects")
    schema_version = envelope.get("schema_version")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool):
        raise ValueError("envelope.schema_version must be an integer")
    if schema_version > current_schema_version:
        raise ValueError(
            f"export schema_version {schema_version} is newer than this install's "
            f"{current_schema_version}; upgrade the app before importing"
        )


async def _business_table_columns(db: Any) -> dict[str, list[str]]:
    """Map every business table name -> ordered column-name whitelist."""
    schema_rows = await db.fetchall(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    columns: dict[str, list[str]] = {}
    for row in schema_rows:
        name = row["name"]
        if name in _EXCLUDED_TABLES or _is_virtual_table(row["sql"]):
            continue
        info = await db.fetchall(f'PRAGMA table_info("{name}")')
        columns[name] = [r["name"] for r in info]
    return columns


async def restore_database(db: Any, envelope: dict[str, Any]) -> dict[str, Any]:
    """Replace-import *envelope* into *db* inside a single transaction.

    Semantics (R22 scope):

    * **Replace, not merge** — every table present in the envelope is
      ``DELETE``-d fully, then repopulated from the envelope rows. Tables
      absent from the envelope keep their current contents, which makes
      re-importing the same file idempotent (import twice == import once).
    * **Column whitelist** — row keys are intersected with the target
      table's ``PRAGMA table_info`` columns; unknown keys (schema drift
      between export and import) are dropped, missing columns fall back
      to SQL defaults. Values are always bound parameters.
    * **All-or-nothing** — the whole import runs in one ``BEGIN IMMEDIATE``
      transaction; any failure rolls the database back untouched.

    Returns ``{imported: {table: rows}, skipped_tables: [...]}``.
    """
    current = await db.applied_versions()
    _validate_envelope(envelope, max(current) if current else 0)

    columns_by_table = await _business_table_columns(db)
    envelope_tables = envelope["tables"]
    protected = {"sessions", "messages", "agent_runs", "agent_run_steps", "run_events",
                 "schedule_occurrences", "scheduled_jobs", "tasks", "agent_root_admissions",
                 "research_sources"}
    if max(current, default=0) >= 37 and any(
        name.startswith("budget_") or name in protected for name in envelope_tables
    ):
        raise ValueError(
            "Budget/run history JSON restore is unsupported on Schema037; "
            "use a reviewed offline SQLite restore and budget reconciliation flow. "
            "Unrelated settings and memory imports remain supported."
        )
    if max(current, default=0) >= 39 and any(
        name in {"execution_installation", "process_executions", "process_execution_events"}
        for name in envelope_tables
    ):
        raise ValueError(
            "Process installation/ownership JSON restore is unsupported on Schema039; "
            "restore the complete SQLite database offline with its original private marker "
            "and reconcile exact container responsibility before accepting new work."
        )
    if max(current, default=0) >= 48 and (
        {"webhooks", "webhook_deliveries"} & envelope_tables.keys()
    ):
        raise ValueError(
            "Webhook configuration/delivery ledger JSON restore is unsupported on Schema048; "
            "use a reviewed offline SQLite restore and reconcile side effects "
            "before accepting webhook deliveries."
        )

    imported: dict[str, int] = {}
    skipped: list[str] = []
    async with db.import_transaction() as conn:
        # Refuse to conceal pre-existing corruption or create new dangling
        # references, including references from tables absent in the envelope.
        async with conn.execute("PRAGMA foreign_key_check") as cursor:
            if await cursor.fetchone():
                raise ValueError("Existing foreign key violations prevent import")
        # Older exports have timeline state but no durable event history.
        # Do not attach this installation's previous events to imported runs.
        if ("run_events" in columns_by_table and "run_events" not in envelope_tables
                and {"agent_runs", "agent_run_steps"} & envelope_tables.keys()):
            await conn.execute("DELETE FROM run_events")
        for table in sorted(envelope_tables):
            if table not in columns_by_table:
                # Table no longer exists in this install (schema drift).
                skipped.append(table)
                continue
            whitelist = columns_by_table[table]
            # Column set = whitelist ∩ union of envelope row keys, so
            # drifted columns are dropped and columns absent from every
            # row keep their SQL DEFAULT instead of a forced NULL.
            keys = set()
            for row in envelope_tables[table]:
                keys.update(row)
            cols = [c for c in whitelist if c in keys]
            if not cols:
                # Envelope rows carry no known columns (e.g. an empty
                # table dumped as [{}]) — just truncate.
                await conn.execute(f'DELETE FROM "{table}"')  # noqa: S608
                imported[table] = 0
                continue
            # Column names come from PRAGMA table_info (trusted schema
            # metadata), never from the envelope — not injectable.
            col_list = ", ".join(f'"{c}"' for c in cols)
            placeholders = ", ".join("?" for _ in cols)
            # ``table`` was matched against the whitelist above.
            await conn.execute(f'DELETE FROM "{table}"')  # noqa: S608
            payload = [
                tuple(row.get(c) for c in cols) for row in envelope_tables[table]
            ]
            if payload:
                await conn.executemany(
                    f'INSERT INTO "{table}" ({col_list}) '  # noqa: S608
                    f"VALUES ({placeholders})",
                    payload,
                )
            imported[table] = len(payload)

        async with conn.execute("PRAGMA foreign_key_check") as cursor:
            if await cursor.fetchone():
                raise ValueError("Import would cause foreign key violations")

    logger.info(
        "data.import: %d tables, %d rows total, %d skipped",
        len(imported),
        sum(imported.values()),
        len(skipped),
    )
    return {"imported": imported, "skipped_tables": skipped}


# ---------------------------------------------------------------------------
# Backup (R23)
# ---------------------------------------------------------------------------


async def backup_database(db: Any, target_dir: Path | str | None = None, *,
                          require_existing_dir: bool = False) -> dict[str, Any]:
    """Hot-copy *db* to a timestamped file via the SQLite backup API.

    Unlike the JSON export, the backup is a **file-level complete snapshot**
    — schema, WAL contents, FTS indexes and vec shadows included — taken
    online without blocking readers. The source database is only ever
    read, never written.
    """
    from ..storage.db import default_data_dir

    dest_dir = Path(target_dir).expanduser() if target_dir else default_data_dir() / "backups"
    if require_existing_dir:
        if not dest_dir.is_absolute() or not dest_dir.is_dir():
            raise ValueError("backup directory must be an existing absolute path on the agent host")
    else:
        dest_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    # Clock resolution is not uniqueness: two backups can share a millisecond.
    # Exclusive creation also prevents concurrent requests overwriting a prior
    # snapshot. Keep the timestamp for human sorting and add a random suffix.
    descriptor, filename = tempfile.mkstemp(
        prefix=f"minimax-code-backup-{now:%Y%m%d-%H%M%S}-", suffix=".db", dir=dest_dir,
    )
    os.close(descriptor)
    dest = Path(filename)

    try:
        target = sqlite3.connect(str(dest))
        try:
            await db.backup(target)
        finally:
            target.close()
    except BaseException:
        dest.unlink(missing_ok=True)
        raise
    return {"path": str(dest), "bytes": dest.stat().st_size}


def list_backups(target_dir: str | None = None) -> dict[str, Any]:
    """List up to 100 newest named SQLite backups without opening their contents."""
    from ..storage.db import default_data_dir

    directory = Path(target_dir).expanduser() if target_dir else default_data_dir() / "backups"
    if target_dir and (not directory.is_absolute() or not directory.is_dir()):
        raise ValueError("backup directory must be an existing absolute path on the agent host")
    if not directory.exists() and not target_dir:
        return {"directory": str(directory), "items": [], "truncated": False}
    candidates = []
    with os.scandir(directory) as entries:
        for entry in entries:
            if not entry.name.startswith("minimax-code-backup-") or not entry.name.endswith(".db"):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue  # A concurrent removal is not an inventory failure.
            if not stat.S_ISREG(info.st_mode):
                continue
            item = (info.st_mtime_ns, entry.name, info.st_size)
            if len(candidates) < 101:
                heapq.heappush(candidates, item)
            elif item > candidates[0]:
                heapq.heapreplace(candidates, item)
    newest = sorted(candidates, reverse=True)
    return {
        "directory": str(directory),
        "items": [
            {"path": str(directory / name), "bytes": size,
             "modified_at": datetime.fromtimestamp(ns / 1_000_000_000, UTC).isoformat()}
            for ns, name, size in newest[:100]
        ],
        "truncated": len(newest) > 100,
    }


async def _handle_backup_list(params: Any, ctx: Context) -> None:
    try:
        request = DataBackupListParams.model_validate(params if params is not None else {})
        if request.target_dir is not None and not request.target_dir.strip():
            raise ValueError("target_dir must be a nonempty path")
        await ctx.reply(list_backups(request.target_dir))
    except ValidationError:
        await ctx.reply_error(INVALID_PARAMS, "Invalid backup list parameters")
    except ValueError as exc:
        await ctx.reply_error(INVALID_PARAMS, str(exc))
    except OSError:
        logger.exception("data.backup_list failed")
        await ctx.reply_error(INTERNAL_ERROR, "data.backup_list failed")


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def _handle_data_export(params: Any, ctx: Context) -> None:
    """``data.export`` — no parameters; returns the full export envelope."""
    try:
        from ..app import get_db

        db = get_db()
        if db is None:
            await ctx.reply_error(
                INTERNAL_ERROR,
                "storage not initialised; nothing to export",
            )
            return
        envelope = await dump_database(db)
        await ctx.reply(envelope)
        logger.info(
            "data.export: %d tables, %d rows total",
            len(envelope["counts"]),
            sum(envelope["counts"].values()),
        )
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message)
    except Exception:
        logger.exception("data.export failed")
        await ctx.reply_error(INTERNAL_ERROR, "data.export failed")


async def _handle_data_import(params: Any, ctx: Context) -> None:
    """``data.import`` — params ``{envelope}``; replace-imports it."""
    try:
        from ..app import get_db

        db = get_db()
        if db is None:
            await ctx.reply_error(
                INTERNAL_ERROR,
                "storage not initialised; nowhere to import",
            )
            return
        envelope = (params or {}).get("envelope") if isinstance(params, dict) else None
        if envelope is None:
            await ctx.reply_error(
                INVALID_PARAMS, "data.import requires an 'envelope' object"
            )
            return
        summary = await restore_database(db, envelope)
        await ctx.reply(summary)
    except ValueError as exc:
        # Envelope validation failure — caller's fault, not an internal error.
        await ctx.reply_error(INVALID_PARAMS, str(exc))
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message)
    except Exception:
        logger.exception("data.import failed")
        await ctx.reply_error(INTERNAL_ERROR, "data.import failed")


async def _handle_data_backup(params: Any, ctx: Context) -> None:
    """``data.backup`` — params ``{target_dir?}``; returns `{path, bytes}`."""
    try:
        from ..app import get_db

        db = get_db()
        if db is None:
            await ctx.reply_error(
                INTERNAL_ERROR,
                "storage not initialised; nothing to back up",
            )
            return
        target_dir = None
        if isinstance(params, dict) and params.get("target_dir") is not None:
            target_dir = str(params["target_dir"])
        require_existing = isinstance(params, dict) and params.get("require_existing_dir") is True
        result = await backup_database(db, target_dir, require_existing_dir=require_existing)
        await ctx.reply(result)
        logger.info("data.backup: %s (%d bytes)", result["path"], result["bytes"])
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message)
    except ValueError as exc:
        await ctx.reply_error(INVALID_PARAMS, str(exc))
    except Exception:
        logger.exception("data.backup failed")
        await ctx.reply_error(INTERNAL_ERROR, "data.backup failed")


def register_data_handlers(server: Any) -> None:
    """Register all ``data.*`` JSON-RPC methods on *server*."""
    server.register("data.export", _handle_data_export)
    server.register("data.import", _handle_data_import)
    server.register("data.backup", _handle_data_backup)
    server.register("data.backup_list", _handle_backup_list)
    logger.debug("registered data.* handlers")


__all__ = [
    "EXPORT_FORMAT",
    "backup_database",
    "dump_database",
    "register_data_handlers",
    "restore_database",
]
