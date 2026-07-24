"""IPC handlers — ``audit.*`` namespace.

Provides read-only access to the tool-call audit trail plus an
administrative purge endpoint.

Methods
-------

``audit.list``
    Paginated list of recent audit entries.
``audit.stats``
    Aggregate counts by tool name and result status.
``audit.purge``
    Delete entries older than a given ISO-8601 timestamp.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .handler_utils import HandlerError
from .protocol import INTERNAL_ERROR
from .server import Context

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy DAO factory (same pattern as handlers_scheduled.py)
# ---------------------------------------------------------------------------

_DAO_ATTR = "_audit_log_dao"
_DAO_LOCK_ATTR = "_audit_log_dao_lock"

def _make_audit_dao_factory(server: Any):
    """Return an async factory that lazily creates an ``AuditLogDAO``."""

    async def _factory() -> Any:
        from ..storage.dao.audit import AuditLogDAO

        existing = getattr(server, _DAO_ATTR, None)
        if existing is not None:
            return existing
        lock = getattr(server, _DAO_LOCK_ATTR, None)
        if lock is None:
            lock = asyncio.Lock()
            setattr(server, _DAO_LOCK_ATTR, lock)
        async with lock:
            existing = getattr(server, _DAO_ATTR, None)
            if existing is not None:
                return existing
            # Use the process-wide DB singleton — avoid opening extra
            # connections that are never closed (connection leak).
            try:
                from ..app import get_db

                db = get_db()
            except Exception:
                db = None
            if db is None:
                logger.warning("storage not initialised; audit DAO unavailable")
                return None
            dao = AuditLogDAO(db)
            setattr(server, _DAO_ATTR, dao)
            return dao

    return _factory

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def _handle_audit_list(params: Any, ctx: Context) -> None:
    try:
        dao = await _ensure_dao(ctx)
        if dao is None:
            await ctx.reply({"entries": [], "total": 0})
            return
        if not isinstance(params, dict):
            params = {}
        limit = int(params.get("limit", 100))
        offset = int(params.get("offset", 0))
        tool_name = params.get("tool_name")
        session_id = params.get("session_id")
        entries = await dao.list_recent(
            limit=limit, offset=offset,
            tool_name=tool_name, session_id=session_id,
        )
        total = await dao.count(
            tool_name=tool_name, session_id=session_id,
        )
        await ctx.reply({"entries": entries, "total": total})
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message)
    except Exception:
        logger.exception("audit.list failed")
        await ctx.reply_error(INTERNAL_ERROR, "audit.list failed")

async def _handle_audit_stats(params: Any, ctx: Context) -> None:
    try:
        dao = await _ensure_dao(ctx)
        if dao is None:
            await ctx.reply({"total": 0, "by_tool": {}, "by_status": {}})
            return
        result = await dao.stats()
        await ctx.reply(result)
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message)
    except Exception:
        logger.exception("audit.stats failed")
        await ctx.reply_error(INTERNAL_ERROR, "audit.stats failed")

async def _handle_audit_purge(params: Any, ctx: Context) -> None:
    try:
        dao = await _ensure_dao(ctx)
        if dao is None:
            await ctx.reply({"deleted": 0})
            return
        if not isinstance(params, dict) or "before_iso" not in params:
            await ctx.reply_error(-32602, "before_iso is required")
            return
        deleted = await dao.purge_before(params["before_iso"])
        await ctx.reply({"deleted": deleted})
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message)
    except Exception:
        logger.exception("audit.purge failed")
        await ctx.reply_error(INTERNAL_ERROR, "audit.purge failed")

# ---------------------------------------------------------------------------
# DAO accessor
# ---------------------------------------------------------------------------

_FACTORY_ATTR = "_audit_log_dao_factory"

def _ensure_dao(ctx: Context) -> Any:
    """Return the DAO via the lazy factory stored on the server."""
    factory = getattr(ctx.server, _FACTORY_ATTR, None)
    if factory is None:
        factory = _make_audit_dao_factory(ctx.server)
        setattr(ctx.server, _FACTORY_ATTR, factory)
    return factory()

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_audit_handlers(server: Any) -> None:
    """Register all ``audit.*`` JSON-RPC methods on *server*."""
    server.register("audit.list", _handle_audit_list)
    server.register("audit.stats", _handle_audit_stats)
    server.register("audit.purge", _handle_audit_purge)
    logger.debug("registered audit.* handlers")

__all__ = ["register_audit_handlers"]
