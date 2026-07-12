"""JSON-RPC handlers for the ``provider.*`` namespace.

These wrap :class:`~minimax_code.storage.dao.providers.ProviderDAO` and
:mod:`minimax_code.secrets` so the Settings page can manage LLM
providers — list them, create new ones, edit settings, delete them,
and write / clear per-provider API keys.

Endpoints
---------
``provider.list``           -> ``{"providers": [...]}``
``provider.get``            -> ``{"provider": {...}}`` | error
``provider.create``         -> ``{"provider": {...}}``
``provider.update``         -> ``{"provider": {...}}``
``provider.delete``         -> ``{"ok": true}``
``provider.set_api_key``    -> ``{"ok": true, "api_key_configured": true}``
``provider.clear_api_key``  -> ``{"ok": true, "api_key_configured": false}``

Wire-up
-------
:func:`register_provider_handlers` is called from
:func:`minimax_code.app.register_app_handlers`. The DAO is lazily
built on the first call (same pattern as ``model.*`` and
``permission.*`` handlers).
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
from .handler_utils import HandlerError, check_params
from .server import Context

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# DAO lazy-build (same pattern as handlers_model / handlers_permissions)
# ---------------------------------------------------------------------------

_DAO_ATTR = "_provider_dao"
_LOCK_ATTR = "_provider_dao_lock"

async def _ensure_dao(server: Any) -> Any:
    """Return a cached :class:`ProviderDAO`, creating one on first call."""
    existing = getattr(server, _DAO_ATTR, None)
    if existing is not None:
        return existing
    lock = getattr(server, _LOCK_ATTR, None)
    if lock is None:
        lock = asyncio.Lock()
        setattr(server, _LOCK_ATTR, lock)
    async with lock:
        existing = getattr(server, _DAO_ATTR, None)
        if existing is not None:
            return existing
        from ..app import ensure_db
        from ..storage.dao.providers import ProviderDAO

        if os.environ.get("MINIMAX_CODE_NO_DB") == "1":
            raise HandlerError(
                STORAGE_ERROR,
                "storage is disabled (MINIMAX_CODE_NO_DB=1); "
                "provider handlers need a DB",
            )
        try:
            db = await ensure_db()
            if db is None:
                raise RuntimeError("storage is unavailable")
        except Exception as exc:
            logger.exception("failed to open storage for provider handlers")
            raise HandlerError(
                STORAGE_ERROR, f"failed to open storage: {exc}"
            ) from exc
        dao_obj = ProviderDAO(db)
        setattr(server, _DAO_ATTR, dao_obj)
        return dao_obj

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_provider_handlers(server: Any, dao: Any = None) -> None:
    """Register the ``provider.*`` handlers on ``server``.

    Parameters
    ----------
    server:
        The :class:`~minimax_code.ipc.server.IPCServer` instance.
    dao:
        Optional pre-built :class:`~.ProviderDAO`. When ``None``,
        the first ``provider.*`` call opens the async database and
        constructs the DAO lazily.
    """
    if dao is not None:
        setattr(server, _DAO_ATTR, dao)
        setattr(server, _LOCK_ATTR, asyncio.Lock())

    # ---- provider.list -----------------------------------------------------

    async def handle_provider_list(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys=set())
            dao = await _ensure_dao(ctx.server)
            providers = await dao.list()
            # Annotate each provider with ``api_key_configured`` from
            # keyring / env without exposing the actual key value.
            from .. import secrets

            for p in providers:
                p["api_key_configured"] = secrets.has_provider_key(p["id"])
            await ctx.reply({"providers": providers})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            logger.exception("provider.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "provider.list failed")

    # ---- provider.get ------------------------------------------------------

    async def handle_provider_get(params: Any, ctx: Context) -> None:
        try:
            dao = await _ensure_dao(ctx.server)
            check_params(params, expected_keys={"provider_id"})
            provider_id = str(params["provider_id"]).strip()
            provider = await dao.get(provider_id)
            if provider is None:
                raise HandlerError(
                    INVALID_PARAMS,
                    f"provider {provider_id!r} not found",
                )
            from .. import secrets

            provider["api_key_configured"] = secrets.has_provider_key(provider_id)
            await ctx.reply({"provider": provider})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            logger.exception("provider.get failed")
            await ctx.reply_error(INTERNAL_ERROR, "provider.get failed")

    # ---- provider.create ---------------------------------------------------

    async def handle_provider_create(params: Any, ctx: Context) -> None:
        try:
            dao = await _ensure_dao(ctx.server)
            check_params(params, expected_keys={"name", "protocol", "base_url"})
            name = str(params["name"]).strip()
            protocol = str(params["protocol"]).strip().lower()
            base_url = str(params["base_url"]).strip()
            if not name:
                raise HandlerError(INVALID_PARAMS, "'name' must be non-empty")
            if protocol not in ("anthropic", "openai"):
                raise HandlerError(
                    INVALID_PARAMS,
                    f"unsupported protocol {protocol!r}; "
                    "must be 'anthropic' or 'openai'",
                )
            if not base_url:
                raise HandlerError(INVALID_PARAMS, "'base_url' must be non-empty")

            models = params.get("models")
            if models is not None and not isinstance(models, list):
                raise HandlerError(INVALID_PARAMS, "'models' must be a list")
            enabled = params.get("enabled", True)
            if isinstance(enabled, str):
                enabled = enabled.lower() in ("true", "1", "yes")

            provider = await dao.create(
                name=name,
                protocol=protocol,
                base_url=base_url,
                models=models if isinstance(models, list) else None,
                enabled=bool(enabled),
            )

            # Optionally persist API key in the same call.
            api_key = params.get("api_key")
            if api_key and isinstance(api_key, str) and api_key.strip():
                from .. import secrets

                secrets.set_provider_key(provider["id"], api_key.strip())
                # Update api_key_set flag in DB.
                await dao.update(provider["id"], api_key_set=1)
                provider["api_key_set"] = 1

            from .. import secrets

            provider["api_key_configured"] = secrets.has_provider_key(provider["id"])

            # Rebuild sub-agent LLM so the next agent.send_message
            # picks up the new provider's models.
            try:
                from ..app import rebuild_subagent_llm
                await rebuild_subagent_llm()
            except Exception:
                logger.debug("rebuild_subagent_llm after provider.create failed")

            await ctx.reply({"provider": provider})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            logger.exception("provider.create failed")
            await ctx.reply_error(INTERNAL_ERROR, "provider.create failed")

    # ---- provider.update ---------------------------------------------------

    async def handle_provider_update(params: Any, ctx: Context) -> None:
        try:
            dao = await _ensure_dao(ctx.server)
            check_params(params, expected_keys={"provider_id"})
            provider_id = str(params["provider_id"]).strip()
            existing = await dao.get(provider_id)
            if existing is None:
                raise HandlerError(
                    INVALID_PARAMS,
                    f"provider {provider_id!r} not found",
                )

            kwargs: dict[str, Any] = {}
            if "name" in params and params["name"] is not None:
                kwargs["name"] = str(params["name"]).strip()
            if "protocol" in params and params["protocol"] is not None:
                protocol = str(params["protocol"]).strip().lower()
                if protocol not in ("anthropic", "openai"):
                    raise HandlerError(
                        INVALID_PARAMS,
                        f"unsupported protocol {protocol!r}",
                    )
                kwargs["protocol"] = protocol
            if "base_url" in params and params["base_url"] is not None:
                kwargs["base_url"] = str(params["base_url"]).strip()
            if "models" in params and params["models"] is not None:
                models = params["models"]
                if not isinstance(models, list):
                    raise HandlerError(INVALID_PARAMS, "'models' must be a list")
                kwargs["models"] = models
            if "enabled" in params and params["enabled"] is not None:
                enabled = params["enabled"]
                if isinstance(enabled, str):
                    enabled = enabled.lower() in ("true", "1", "yes")
                kwargs["enabled"] = bool(enabled)

            # Optionally update API key.
            api_key = params.get("api_key")
            if api_key is not None:
                from .. import secrets

                if isinstance(api_key, str) and api_key.strip():
                    secrets.set_provider_key(provider_id, api_key.strip())
                    kwargs["api_key_set"] = 1
                else:
                    # Empty string = clear key.
                    try:
                        secrets.clear_provider_key(provider_id)
                    except Exception:
                        logger.debug("clear_provider_key failed; continuing")
                    kwargs["api_key_set"] = 0

            provider = await dao.update(provider_id, **kwargs)
            from .. import secrets

            provider["api_key_configured"] = secrets.has_provider_key(provider_id)

            # Rebuild sub-agent LLM — base_url / protocol / key may have
            # changed, and the model list may differ now.
            try:
                from ..app import rebuild_subagent_llm
                await rebuild_subagent_llm()
            except Exception:
                logger.debug("rebuild_subagent_llm after provider.update failed")

            await ctx.reply({"provider": provider})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            logger.exception("provider.update failed")
            await ctx.reply_error(INTERNAL_ERROR, "provider.update failed")

    # ---- provider.delete ---------------------------------------------------

    async def handle_provider_delete(params: Any, ctx: Context) -> None:
        try:
            dao = await _ensure_dao(ctx.server)
            check_params(params, expected_keys={"provider_id"})
            provider_id = str(params["provider_id"]).strip()
            existing = await dao.get(provider_id)
            if existing is None:
                raise HandlerError(
                    INVALID_PARAMS,
                    f"provider {provider_id!r} not found",
                )
            # Guard: refuse to delete the built-in provider.
            if provider_id == "builtin-minimax":
                raise HandlerError(
                    INVALID_PARAMS,
                    "cannot delete the built-in MiniMax provider",
                )
            # Clean up keyring first, then DB row.
            try:
                from .. import secrets

                secrets.clear_provider_key(provider_id)
            except Exception:
                logger.debug("clear_provider_key(%s) failed; continuing", provider_id)
            await dao.delete(provider_id)

            # Rebuild sub-agent LLM — the deleted provider's models are
            # gone from the dynamic list; active config must refresh.
            try:
                from ..app import rebuild_subagent_llm
                await rebuild_subagent_llm()
            except Exception:
                logger.debug("rebuild_subagent_llm after provider.delete failed")

            await ctx.reply({"ok": True, "deleted": provider_id})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            logger.exception("provider.delete failed")
            await ctx.reply_error(INTERNAL_ERROR, "provider.delete failed")

    # ---- provider.set_api_key ----------------------------------------------

    async def handle_provider_set_api_key(params: Any, ctx: Context) -> None:
        try:
            dao = await _ensure_dao(ctx.server)
            check_params(params, expected_keys={"provider_id", "api_key"})
            provider_id = str(params["provider_id"]).strip()
            api_key = str(params["api_key"]).strip()
            if not api_key:
                raise HandlerError(INVALID_PARAMS, "'api_key' must be non-empty")
            existing = await dao.get(provider_id)
            if existing is None:
                raise HandlerError(
                    INVALID_PARAMS,
                    f"provider {provider_id!r} not found",
                )
            from .. import secrets

            secrets.set_provider_key(provider_id, api_key)
            await dao.update(provider_id, api_key_set=1)
            try:
                from ..app import rebuild_subagent_llm

                await rebuild_subagent_llm()
            except Exception:
                logger.debug("rebuild_subagent_llm after provider.set_api_key failed")
            await ctx.reply(
                {"ok": True, "provider_id": provider_id, "api_key_configured": True}
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            logger.exception("provider.set_api_key failed")
            await ctx.reply_error(
                INTERNAL_ERROR, f"provider.set_api_key failed: {exc}"
            )

    # ---- provider.clear_api_key --------------------------------------------

    async def handle_provider_clear_api_key(params: Any, ctx: Context) -> None:
        try:
            dao = await _ensure_dao(ctx.server)
            check_params(params, expected_keys={"provider_id"})
            provider_id = str(params["provider_id"]).strip()
            existing = await dao.get(provider_id)
            if existing is None:
                raise HandlerError(
                    INVALID_PARAMS,
                    f"provider {provider_id!r} not found",
                )
            from .. import secrets

            try:
                secrets.clear_provider_key(provider_id)
            except Exception:
                logger.debug("clear_provider_key(%s) failed; continuing", provider_id)
            await dao.update(provider_id, api_key_set=0)
            try:
                from ..app import rebuild_subagent_llm

                await rebuild_subagent_llm()
            except Exception:
                logger.debug("rebuild_subagent_llm after provider.clear_api_key failed")
            await ctx.reply(
                {"ok": True, "provider_id": provider_id, "api_key_configured": False}
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            logger.exception("provider.clear_api_key failed")
            await ctx.reply_error(
                INTERNAL_ERROR, f"provider.clear_api_key failed: {exc}"
            )

    server.register("provider.list", handle_provider_list)
    server.register("provider.get", handle_provider_get)
    server.register("provider.create", handle_provider_create)
    server.register("provider.update", handle_provider_update)
    server.register("provider.delete", handle_provider_delete)
    server.register("provider.set_api_key", handle_provider_set_api_key)
    server.register("provider.clear_api_key", handle_provider_clear_api_key)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

__all__ = ["register_provider_handlers"]
