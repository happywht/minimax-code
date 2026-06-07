"""IPC handlers — ``notification.*`` namespace.

Provides CRUD + mark-read + purge over JSON-RPC 2.0.

Methods
-------
notification.list          Paginated list with filters
notification.mark_read     Mark single notification as read
notification.mark_all_read Mark all unread as read
notification.delete        Delete a single notification
notification.purge         Bulk-delete by criteria
"""

from __future__ import annotations

from .handler_utils import HandlerError
import asyncio
import logging
from typing import Any

from ..ipc.server import Context
from ..storage.dao.notifications import NotificationDAO

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy DAO factory (same pattern as handlers_audit.py)
# ---------------------------------------------------------------------------

_DAO_ATTR = "_notification_dao"
_DAO_LOCK_ATTR = "_notification_dao_lock"
_FACTORY_ATTR = "_notification_dao_factory"

def _make_notification_dao_factory(server: Any) -> Any:
    """Return (and cache) an async factory that produces ``NotificationDAO``."""
    factory = getattr(server, _FACTORY_ATTR, None)
    if factory is not None:
        return factory

    async def _factory() -> NotificationDAO:
        dao = getattr(server, _DAO_ATTR, None)
        if dao is not None:
            return dao
        lock: asyncio.Lock = getattr(server, _DAO_LOCK_ATTR, asyncio.Lock())
        setattr(server, _DAO_LOCK_ATTR, lock)
        async with lock:
            dao = getattr(server, _DAO_ATTR, None)
            if dao is not None:
                return dao
            # Use the process-wide DB singleton — never open a
            # second connection (avoids connection leaks and
            # ``AsyncDatabase()`` without a path).
            from ..app import get_db

            db = get_db()
            if db is None:
                raise HandlerError(
                    -32004, "storage not initialised"
                )
            dao = NotificationDAO(db)
            setattr(server, _DAO_ATTR, dao)
            return dao

    setattr(server, _FACTORY_ATTR, _factory)
    return _factory

async def _ensure_dao(ctx: Context) -> NotificationDAO:
    factory = _make_notification_dao_factory(ctx.server)
    return await factory()

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_notification_list(params: dict[str, Any], ctx: Context) -> dict[str, Any]:
    dao = await _ensure_dao(ctx)
    limit = int(params.get("limit", 100))
    offset = int(params.get("offset", 0))
    ntype = params.get("type")
    source = params.get("source")
    unread_only = bool(params.get("unread_only", False))

    entries, total = await dao.list_all(
        limit=limit,
        offset=offset,
        type=ntype,
        source=source,
        unread_only=unread_only,
    )
    return {"entries": entries, "total": total}

async def handle_notification_mark_read(params: dict[str, Any], ctx: Context) -> dict[str, Any]:
    dao = await _ensure_dao(ctx)
    nid = params.get("id")
    if not nid:
        raise HandlerError(-32001, "Missing 'id'")
    entry = await dao.mark_read(nid)
    if entry is None:
        raise HandlerError(-32002, f"Notification '{nid}' not found")
    # Push read event so other clients can update UI
    try:
        ctx.server.notify({"event": "notification.read", "data": entry})
    except Exception:
        logger.warning("Failed to push notification.read event", exc_info=True)
    return entry

async def handle_notification_mark_all_read(params: dict[str, Any], ctx: Context) -> dict[str, Any]:
    dao = await _ensure_dao(ctx)
    count = await dao.mark_all_read()
    return {"marked": count}

async def handle_notification_delete(params: dict[str, Any], ctx: Context) -> dict[str, Any]:
    dao = await _ensure_dao(ctx)
    nid = params.get("id")
    if not nid:
        raise HandlerError(-32001, "Missing 'id'")
    ok = await dao.delete(nid)
    if not ok:
        raise HandlerError(-32002, f"Notification '{nid}' not found")
    return {"deleted": True}

async def handle_notification_purge(params: dict[str, Any], ctx: Context) -> dict[str, Any]:
    dao = await _ensure_dao(ctx)
    before_iso = params.get("before_iso")
    read_only = bool(params.get("read_only", False))
    count = await dao.purge(before_iso=before_iso, read_only=read_only)
    return {"purged": count}

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_notification_handlers(server: Any) -> None:
    server.register("notification.list", handle_notification_list)
    server.register("notification.mark_read", handle_notification_mark_read)
    server.register("notification.mark_all_read", handle_notification_mark_all_read)
    server.register("notification.delete", handle_notification_delete)
    server.register("notification.purge", handle_notification_purge)
    logger.info("Registered notification.* handlers")

__all__ = ["register_notification_handlers"]
