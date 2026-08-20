"""IPC handlers — ``diag.*`` namespace (diagnostics, M8 / R45).

``diag.export``
    Assemble a sanitized diagnostic bundle as a single JSON document
    (the RPC result itself); the frontend turns it into a downloaded
    file users can attach to a bug report.

Everything in the bundle is safe to share by construction: config is
reported as enums / numbers / booleans / counts (never secret values),
paths are reduced to basenames (the ``/health`` precedent), and the log
tail passes through the same ``SanitizerFilter`` as the stderr sink.
"""

from __future__ import annotations

import logging
import os
import platform as _platform
import sys
import time
from datetime import UTC, datetime
from typing import Any

from .protocol import INTERNAL_ERROR
from .server import Context

logger = logging.getLogger(__name__)

#: Stable discriminator for the diagnostic bundle.
DIAG_FORMAT = "minimax-code-diagnostic"

#: How many recent log lines to embed in the bundle.
LOG_TAIL_LINES = 200

#: Best-effort process-start reference. This module imports during
#: handler registration, early enough that the uptime figure is accurate
#: to within a second of process start.
_EPOCH = time.time()

#: Config attributes that are safe to report verbatim (enums / numbers).
_SAFE_CONFIG_KEYS = (
    "log_level",
    "env",
    "session_idle_timeout_seconds",
    "max_message_bytes",
)


def _int_env(name: str, default: int) -> int | None:
    """Parse an int env var; a malformed value reports as ``None``."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return None


def _platform_section() -> dict[str, Any]:
    return {
        "system": _platform.system(),
        "release": _platform.release(),
        "machine": _platform.machine(),
        "python": sys.version.split()[0],
        "pid": os.getpid(),
    }


def _config_section() -> dict[str, Any]:
    """Sanitized runtime config: enums, numbers, booleans, counts only.

    Secrets never enter this section by construction — no keyring reads,
    no provider credentials, no CORS origin values. Paths are reduced to
    basenames so no absolute user path can leak into a bug-report
    attachment (the ``/health`` ``data_dir`` precedent).
    """
    from ..config import Config
    from ..storage.db import default_data_dir

    try:
        cfg = Config.from_env()
        safe: dict[str, Any] = {key: getattr(cfg, key) for key in _SAFE_CONFIG_KEYS}
    except Exception:  # noqa: BLE001 - malformed env is itself diagnostic
        safe = {"config_parse_error": True}
    cors_raw = os.environ.get("MINIMAX_CODE_CORS_ORIGINS", "").strip()
    return {
        **safe,
        "http_host": os.environ.get("MINIMAX_CODE_HTTP_HOST", "127.0.0.1"),
        "http_port": _int_env("MINIMAX_CODE_HTTP_PORT", 8765),
        "cors_custom_origin_count": (
            len([o for o in cors_raw.split(",") if o.strip()]) if cors_raw else 0
        ),
        "log_file_configured": bool(os.environ.get("MINIMAX_CODE_LOG_FILE", "").strip()),
        "data_dir_name": default_data_dir().name or None,
        "skills_dir_custom": "MINIMAX_CODE_SKILLS_DIR" in os.environ,
    }


def _runtime_section() -> dict[str, Any]:
    return {"uptime_s": int(time.time() - _EPOCH)}


async def _storage_section(db: Any) -> dict[str, Any]:
    """Per-table row counts (same exclusion set as ``data.export``).

    Degrades to ``db_available: False`` without storage
    (``MINIMAX_CODE_NO_DB=1``) — a diagnostic bundle must stay
    exportable precisely when things are broken.
    """
    if db is None:
        return {"db_available": False}
    from .handlers_data import _is_virtual_table

    schema_rows = await db.fetchall(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )
    tables: dict[str, int] = {}
    for row in schema_rows:
        name, sql = row["name"], row["sql"]
        if name == "schema_migrations" or _is_virtual_table(sql):
            continue
        try:
            n = await db.fetchone(f'SELECT COUNT(*) AS n FROM "{name}"')  # noqa: S608
            tables[name] = int(n["n"])
        except Exception:  # noqa: BLE001 - one bad table must not sink the bundle
            tables[name] = -1
    migrations_applied: int | None = None
    try:
        row = await db.fetchone(
            "SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations"
        )
        migrations_applied = int(row["v"])
    except Exception:  # noqa: BLE001
        pass
    return {
        "db_available": True,
        "table_count": len(tables),
        "tables": tables,
        "migrations_applied": migrations_applied,
    }


async def build_diagnostic_bundle(db: Any | None) -> dict[str, Any]:
    """Assemble the sanitized diagnostic bundle for *db* (may be ``None``)."""
    from .. import version as _version
    from ..logging_setup import get_recent_log_lines

    return {
        "format": DIAG_FORMAT,
        "generated_at": datetime.now(UTC).isoformat(),
        "version": _version.installed(),
        "platform": _platform_section(),
        "runtime": _runtime_section(),
        "config": _config_section(),
        "storage": await _storage_section(db),
        "log_tail": get_recent_log_lines(LOG_TAIL_LINES),
    }


async def _handle_diag_export(params: Any, ctx: Context) -> None:
    """``diag.export`` — no parameters; returns the diagnostic bundle."""
    from ..app import get_db

    try:
        bundle = await build_diagnostic_bundle(get_db())
    except Exception:
        logger.exception("diag.export failed")
        await ctx.reply_error(INTERNAL_ERROR, "failed to assemble diagnostic bundle")
        return
    await ctx.reply(bundle)
    logger.info(
        "diag.export: version=%s tables=%s log_tail=%d lines",
        bundle["version"],
        bundle["storage"].get("table_count"),
        len(bundle["log_tail"]),
    )


def register_diag_handlers(server: Any) -> None:
    """Register all ``diag.*`` JSON-RPC methods on *server*."""
    server.register("diag.export", _handle_diag_export)
    logger.debug("registered diag.* handlers")


__all__ = [
    "DIAG_FORMAT",
    "LOG_TAIL_LINES",
    "build_diagnostic_bundle",
    "register_diag_handlers",
]
