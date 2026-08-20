"""IPC handlers — ``data.*`` namespace (data portability, M4 / R21+).

``data.export``
    Dump every business table into a single JSON document (the RPC
    result itself); the frontend turns it into a downloaded file.
    The payload is self-describing: ``format`` / ``schema_version`` /
    ``app_version`` / ``exported_at`` headers plus per-table row lists.

Import (``data.import``) lands in R22 and consumes this exact shape.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from .handler_utils import HandlerError
from .protocol import INTERNAL_ERROR
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


def register_data_handlers(server: Any) -> None:
    """Register all ``data.*`` JSON-RPC methods on *server*."""
    server.register("data.export", _handle_data_export)
    logger.debug("registered data.* handlers")


__all__ = ["EXPORT_FORMAT", "dump_database", "register_data_handlers"]
