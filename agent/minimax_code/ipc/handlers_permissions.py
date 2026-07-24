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
``permission.resolve`` -> ``{"ok": true, "request_id": "..."}``
                          (paired with the ``permission.request`` event
                          emitted by :class:`~.perm_consent.PermissionGater`
                          when a tool is gated)
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from .handler_utils import HandlerError, check_params
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

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_STORE_ATTR = "_permission_store"
_LOCK_ATTR = "_permission_store_lock"

async def _ensure_permission_store(server: Any) -> Any:
    """Resolve the cached :class:`PermissionStore` (build it on first call).

    Public helper — the ``handle_agent_send_message`` flow in
    :mod:`.builtins` calls this so the agent loop can gate tool
    invocations against the same store the IPC handlers expose.
    The factory is idempotent and async-safe (guarded by
    ``server._permission_store_lock``).
    """
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
        from ..app import ensure_db
        from ..permissions import PermissionStore
        from ..storage.dao.permissions import PermissionRuleDAO

        # Honour the same env-var opt-out as app._maybe_open_db so
        # tests / smoke runs can run with storage disabled.
        if os.environ.get("MINIMAX_CODE_NO_DB") == "1":
            raise HandlerError(
                STORAGE_ERROR,
                "storage is disabled (MINIMAX_CODE_NO_DB=1); permission handlers need a DB",
            )
        try:
            db = await ensure_db()
            if db is None:
                raise RuntimeError("storage is unavailable")
        except Exception as exc:
            logger.exception("failed to open storage for permission handlers")
            raise HandlerError(
                STORAGE_ERROR,
                f"failed to open storage: {exc}",
            ) from exc
        dao = PermissionRuleDAO(db)
        store_obj = PermissionStore(dao)
        try:
            await store_obj.warm()
        except Exception as exc:
            logger.exception("permission store warm() failed")
            raise HandlerError(
                INTERNAL_ERROR,
                f"permission store warm failed: {exc}",
            ) from exc
        setattr(server, _STORE_ATTR, store_obj)
        return store_obj

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
        return await _ensure_permission_store(server)

    # ---- handlers ---------------------------------------------------------

    async def handle_permission_list(params: Any, ctx: Context) -> None:
        try:
            store_obj = await _ensure_store()
            check_params(params, expected_keys=set())
            await ctx.reply({"rules": store_obj.list_rules()})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("permission.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "permission.list failed")

    async def handle_permission_get(params: Any, ctx: Context) -> None:
        try:
            store_obj = await _ensure_store()
            check_params(params, expected_keys={"tool_pattern"})
            tool_pattern = str(params["tool_pattern"])
            rule = store_obj.get(tool_pattern)
            await ctx.reply({"rule": rule})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover
            logger.exception("permission.get failed")
            await ctx.reply_error(INTERNAL_ERROR, "permission.get failed")

    async def handle_permission_set(params: Any, ctx: Context) -> None:
        try:
            store_obj = await _ensure_store()
            check_params(params, expected_keys={"tool_pattern", "action"})
            tool_pattern = str(params["tool_pattern"])
            action = str(params["action"])
            scope = str(params.get("scope") or "global")
            rule = await store_obj.upsert(
                tool_pattern=tool_pattern, action=action, scope=scope
            )
            await ctx.reply({"rule": rule})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:  # pragma: no cover
            logger.exception("permission.set failed")
            await ctx.reply_error(INTERNAL_ERROR, "permission.set failed")

    async def handle_permission_delete(params: Any, ctx: Context) -> None:
        try:
            store_obj = await _ensure_store()
            check_params(params, expected_keys={"tool_pattern"})
            tool_pattern = str(params["tool_pattern"])
            deleted = await store_obj.delete(tool_pattern)
            await ctx.reply({"ok": True, "deleted": deleted})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover
            logger.exception("permission.delete failed")
            await ctx.reply_error(INTERNAL_ERROR, "permission.delete failed")

    async def handle_permission_check(params: Any, ctx: Context) -> None:
        try:
            store_obj = await _ensure_store()
            check_params(params, expected_keys={"tool_name"})
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
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover
            logger.exception("permission.check failed")
            await ctx.reply_error(INTERNAL_ERROR, "permission.check failed")

    async def handle_permission_resolve(params: Any, ctx: Context) -> None:
        """Resolve a pending ``permission.request`` event.

        The frontend's permission modal POSTs this when the user
        clicks 允许/拒绝. The handler delegates to the active
        :class:`~.perm_consent.PermissionGater` stashed on the
        server by :func:`minimax_code.ipc.builtins.handle_agent_send_message`.

        Params
        ------
        ``request_id``: opaque id from the ``permission.request`` event.
        ``decision``:   ``"allow"`` or ``"deny"`` (or boolean true/false).

        Reply
        -----
        ``{"ok": true, "request_id": "..."}`` — ``ok=false`` is
        only set on the *error* envelope (unknown request id,
        missing param, etc.) so the frontend can distinguish
        "I made the decision" from "the decision was lost".
        """
        try:
            check_params(params, expected_keys={"request_id", "decision"})
            request_id = str(params["request_id"]).strip()
            if not request_id:
                raise HandlerError(INVALID_PARAMS, "request_id must be a non-empty string")
            decision_raw = params["decision"]
            if isinstance(decision_raw, bool):
                decision = decision_raw
            else:
                decision_str = str(decision_raw).strip().lower()
                if decision_str in ("allow", "yes", "true", "1"):
                    decision = True
                elif decision_str in ("deny", "no", "false", "0"):
                    decision = False
                else:
                    raise HandlerError(
                        INVALID_PARAMS,
                        f"decision must be 'allow' or 'deny' (got {decision_raw!r})",
                    )
            gater = getattr(ctx.server, "_permission_gater", None)
            if gater is None:
                # No gater → the agent loop isn't waiting on a
                # consent decision. Treat as a no-op success so the
                # frontend's modal can close cleanly even after a
                # race (e.g. user clicked after the agent gave up).
                await ctx.reply(
                    {
                        "ok": False,
                        "request_id": request_id,
                        "reason": "no active consent gater",
                    }
                )
                return
            ok = gater.resolve(request_id, decision)
            if not ok:
                await ctx.reply(
                    {
                        "ok": False,
                        "request_id": request_id,
                        "reason": "unknown or already-resolved request_id",
                    }
                )
                return
            await ctx.reply(
                {
                    "ok": True,
                    "request_id": request_id,
                    "decision": "allow" if decision else "deny",
                }
            )
            # Fan-out resolved event so all WS clients (desktop + mobile)
            # can dismiss the consent modal.  This was missing before v0.7.0.
            try:
                ctx.server.notify({
                    "event": "permission.resolved",
                    "data": {
                        "request_id": request_id,
                        "decision": "allow" if decision else "deny",
                    },
                })
            except Exception:
                logger.warning("Failed to push permission.resolved event", exc_info=True)
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("permission.resolve failed")
            await ctx.reply_error(INTERNAL_ERROR, "permission.resolve failed")

    server.register("permission.list", handle_permission_list)
    server.register("permission.get", handle_permission_get)
    server.register("permission.set", handle_permission_set)
    server.register("permission.delete", handle_permission_delete)
    server.register("permission.check", handle_permission_check)
    server.register("permission.resolve", handle_permission_resolve)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

__all__ = ["register_permission_handlers", "_ensure_permission_store"]
