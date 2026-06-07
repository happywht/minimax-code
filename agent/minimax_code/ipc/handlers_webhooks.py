"""IPC handlers — ``webhook.*`` namespace.

Methods:
    webhook.list             — list configured webhooks
    webhook.create           — register a new webhook endpoint
    webhook.update           — modify an existing webhook
    webhook.delete           — remove a webhook
    webhook.regenerate_secret — generate a new HMAC secret

The DAO factory is stored on the server object (NOT as a module-level
global) to avoid test isolation failures when multiple test suites
create and tear down independent IPCServer instances.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..storage.dao.webhooks import WebhookDAO
from .handler_utils import HandlerError as _HandlerError
from .protocol import INVALID_PARAMS

logger = logging.getLogger(__name__)

_FACTORY_ATTR = "_webhook_dao_factory"
_DAO_ATTR = "_webhook_dao"
_LOCK_ATTR = "_webhook_dao_lock"


async def _get_dao(ctx: Any) -> WebhookDAO:
    """Lazy-init a :class:`WebhookDAO` on the server object."""
    dao = getattr(ctx.server, _DAO_ATTR, None)
    if dao is not None:
        return dao
    lock: asyncio.Lock = getattr(ctx.server, _LOCK_ATTR, asyncio.Lock())
    setattr(ctx.server, _LOCK_ATTR, lock)
    async with lock:
        dao = getattr(ctx.server, _DAO_ATTR, None)
        if dao is not None:
            return dao
        factory = getattr(ctx.server, _FACTORY_ATTR, None)
        if factory is not None:
            dao = factory()
        else:
            from ..app import get_db
            db = get_db()
            if db is None:
                raise RuntimeError("storage not initialised")
            dao = WebhookDAO(db)
        setattr(ctx.server, _DAO_ATTR, dao)
    return dao


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _list(params: dict[str, Any], ctx: Any) -> dict[str, Any]:
    dao = await _get_dao(ctx)
    source = params.get("source")
    limit = int(params.get("limit", 100))
    offset = int(params.get("offset", 0))
    entries = await dao.list_all(limit=limit, offset=offset, source=source)
    total = await dao.count(source=source)
    return {"entries": entries, "total": total}


async def _create(params: dict[str, Any], ctx: Any) -> dict[str, Any]:
    dao = await _get_dao(ctx)
    name = params.get("name")
    source = params.get("source", "custom")
    if not name:
        raise _HandlerError(INVALID_PARAMS, "'name' is required")
    action_type = params.get("action_type", "send-message")
    action_config = params.get("action_config")
    secret = params.get("secret")
    return await dao.create(
        name=name,
        source=source,
        secret=secret,
        action_type=action_type,
        action_config=action_config,
    )


async def _update(params: dict[str, Any], ctx: Any) -> dict[str, Any]:
    dao = await _get_dao(ctx)
    webhook_id = params.get("id")
    if not webhook_id:
        raise RuntimeError("'id' is required")
    return await dao.update(
        webhook_id,
        name=params.get("name"),
        source=params.get("source"),
        secret=params.get("secret"),
        enabled=params.get("enabled"),
        action_type=params.get("action_type"),
        action_config=params.get("action_config"),
    )


async def _delete(params: dict[str, Any], ctx: Any) -> dict[str, Any]:
    dao = await _get_dao(ctx)
    webhook_id = params.get("id")
    if not webhook_id:
        raise RuntimeError("'id' is required")
    ok = await dao.delete(webhook_id)
    return {"deleted": ok}


async def _regenerate_secret(params: dict[str, Any], ctx: Any) -> dict[str, Any]:
    dao = await _get_dao(ctx)
    webhook_id = params.get("id")
    if not webhook_id:
        raise RuntimeError("'id' is required")
    return await dao.regenerate_secret(webhook_id)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_webhook_handlers(server: Any) -> None:
    """Register all ``webhook.*`` methods on ``server``."""
    server.register("webhook.list", _list)
    server.register("webhook.create", _create)
    server.register("webhook.update", _update)
    server.register("webhook.delete", _delete)
    server.register("webhook.regenerate_secret", _regenerate_secret)


__all__ = ["register_webhook_handlers"]
