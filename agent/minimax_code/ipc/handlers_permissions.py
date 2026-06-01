"""JSON-RPC handlers for the ``permission.*`` namespace.

The :func:`register_permission_handlers` factory takes an
:class:`~minimax_code.ipc.server.IPCServer` and (optionally) a
pre-built :class:`~minimax_code.permissions.PermissionStore`. When no
store is supplied, handlers lazily open the async database and build
the store on the first call, then cache it on the server instance for
reuse on subsequent requests.

Endpoints
---------
``permission.list``    -> ``{"rules": [...]}``
``permission.get``     -> ``{"rule": {...} | null}``  (by ``tool_pattern``)
``permission.set``     -> ``{"rule": {...}}``         (upsert)
``permission.delete``  -> ``{"ok": true, "deleted": N}``
``permission.check``   -> ``{"allowed": bool, "action": str | null}``
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    STORAGE_ERROR,
)
from .server import Context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class _HandlerError(Exception):
    """Internal sentinel — handlers raise it with a JSON-RPC code."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


_STORE_ATTR = "_permission_store"
_LOCK_ATTR = "_permission_store_lock"


def register_permission_handlers(
    server: Any,
    store: Any = None,
) -> None:
    """Register the ``permission.*`` handlers on ``server``.

    Parameters
    ----------
    server:
        The :class:`~minimax_code.ipc.server.IPCServer` instance.
    store:
        Optional pre-built :class:`~.permissions.PermissionStore`.
        When ``None``, the first ``permission.*`` call opens the
        async database and constructs the store, then caches it on
        ``server._permission_store`` for reuse.
    """

    if store is not None:
        # Stash the store on the server so other code paths can read
        # it without going through the lazy factory again.
        setattr(server, _STORE_ATTR, store)
        setattr(server, _LOCK_ATTR, asyncio.Lock())

    async def _ensure_store() -> Any:
        existing = getattr(server, _STORE_ATTR, None)
        if existing is not None:
            return existing
        lock = getattr(server, _LOCK_ATTR, None)
        if lock is None:
            lock = asyncio.Lock()
            setattr(server, _LOCK_ATTR, lock)
        async with lock:
            existing = getattr(server, _STORE_ATTR, None)
            if existing is not None:
                return existing
            from ..storage.dao.permissions import PermissionRuleDAO
            from ..storage.db import AsyncDatabase, default_database_path
            from ..permissions import PermissionStore

            # Honour the same env-var opt-out as app._maybe_open_db so
            # tests / smoke runs can run with storage disabled.
            if os.environ.get("MINIMAX_CODE_NO_DB") == "1":
                raise _HandlerError(
                    STORAGE_ERROR,
                    "storage is disabled (MINIMAX_CODE_NO_DB=1); permission handlers need a DB",
                )
            db = AsyncDatabase(default_database_path())
            try:
                await db.connect()
                await db.migrate()
            except Exception as exc:
                logger.exception("failed to open storage for permission handlers")
                raise _HandlerError(
                    STORAGE_ERROR,
                    f"failed to open storage: {exc}",
                ) from exc
            dao = PermissionRuleDAO(db)
            store_obj = PermissionStore(dao)
            try:
                await store_obj.warm()
            except Exception as exc:
                logger.exception("permission store warm() failed")
                raise _HandlerError(
                    INTERNAL_ERROR,
                    f"permission store warm failed: {exc}",
                ) from exc
            setattr(server, _STORE_ATTR, store_obj)
            return store_obj

    # ---- handlers ---------------------------------------------------------

    async def handle_permission_list(params: Any, ctx: Context) -> None:
        try:
            store_obj = await _ensure_store()
            _check_params(params, expected_keys=set())
            await ctx.reply({"rules": store_obj.list_rules()})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("permission.list failed")
            await ctx.reply_error(INTERNAL_ERROR, f"permission.list failed: {exc}")

    async def handle_permission_get(params: Any, ctx: Context) -> None:
        try:
            store_obj = await _ensure_store()
            _check_params(params, expected_keys={"tool_pattern"})
            tool_pattern = str(params["tool_pattern"])
            rule = store_obj.get(tool_pattern)
            await ctx.reply({"rule": rule})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover
            logger.exception("permission.get failed")
            await ctx.reply_error(INTERNAL_ERROR, f"permission.get failed: {exc}")

    async def handle_permission_set(params: Any, ctx: Context) -> None:
        try:
            store_obj = await _ensure_store()
            _check_params(params, expected_keys={"tool_pattern", "action"})
            tool_pattern = str(params["tool_pattern"])
            action = str(params["action"])
            scope = str(params.get("scope") or "global")
            rule = await store_obj.upsert(
                tool_pattern=tool_pattern, action=action, scope=scope
            )
            await ctx.reply({"rule": rule})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception as exc:  # pragma: no cover
            logger.exception("permission.set failed")
            await ctx.reply_error(INTERNAL_ERROR, f"permission.set failed: {exc}")

    async def handle_permission_delete(params: Any, ctx: Context) -> None:
        try:
            store_obj = await _ensure_store()
            _check_params(params, expected_keys={"tool_pattern"})
            tool_pattern = str(params["tool_pattern"])
            deleted = await store_obj.delete(tool_pattern)
            await ctx.reply({"ok": True, "deleted": deleted})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover
            logger.exception("permission.delete failed")
            await ctx.reply_error(INTERNAL_ERROR, f"permission.delete failed: {exc}")

    async def handle_permission_check(params: Any, ctx: Context) -> None:
        try:
            store_obj = await _ensure_store()
            _check_params(params, expected_keys={"tool_name"})
            tool_name = str(params["tool_name"])
            scope = str(params.get("scope") or "global")
            allowed = store_obj.is_allowed(tool_name, scope=scope)
            rule = store_obj.lookup(tool_name)
            action = rule.get("action") if rule else None
            await ctx.reply(
                {
                    "allowed": bool(allowed),
                    "action": action,
                    "rule": rule,
                }
            )
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover
            logger.exception("permission.check failed")
            await ctx.reply_error(INTERNAL_ERROR, f"permission.check failed: {exc}")

    server.register("permission.list", handle_permission_list)
    server.register("permission.get", handle_permission_get)
    server.register("permission.set", handle_permission_set)
    server.register("permission.delete", handle_permission_delete)
    server.register("permission.check", handle_permission_check)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _check_params(params: Any, *, expected_keys: set[str]) -> None:
    """Validate the JSON-RPC params shape; raise :class:`_HandlerError` on bad input.

    Rules
    -----
    * ``params`` may be ``None`` (notification-style) only when
      ``expected_keys`` is empty.
    * Otherwise ``params`` must be a dict containing at least
      the keys in ``expected_keys``.
    * Required string values must be non-empty after stripping.
    """
    if not expected_keys:
        return
    if params is None or not isinstance(params, dict):
        raise _HandlerError(
            INVALID_PARAMS,
            "params must be a JSON object with the required keys",
        )
    missing = expected_keys - set(params.keys())
    if missing:
        raise _HandlerError(
            INVALID_PARAMS,
            f"missing required param(s): {sorted(missing)}",
        )
    for key in expected_keys:
        if params[key] is None or (
            isinstance(params[key], str) and not params[key].strip()
        ):
            raise _HandlerError(
                INVALID_PARAMS, f"param {key!r} must be a non-empty value"
            )


__all__ = ["register_permission_handlers"]
