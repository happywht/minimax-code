"""JSON-RPC handlers for the ``model.*`` namespace.

The :func:`register_model_handlers` factory takes an
:class:`~minimax_code.ipc.server.IPCServer` and (optionally) pre-built
DAOs. When no DAO is supplied, handlers lazily open the async database
and build the DAOs on the first call, then cache them on the server
instance for reuse on subsequent requests — same pattern as
:mod:`handlers_permissions`.

Endpoints
---------
``model.list``         -> ``{"models": [...]}``                 (dynamic from providers)
``model.get_current``  -> ``{"model": "..."}``                  (read persistence)
``model.set_current``  -> ``{"ok": true, "model": "..."}``      (write, validated)

Model list
----------

The set of models the user can pick from is built dynamically from
all enabled providers via :class:`~minimax_code.storage.dao.providers.ProviderDAO`.
Each model dict is annotated with ``provider_id``, ``provider_name``,
and ``protocol`` so the frontend can group them by provider.

Validation
----------

``set_current`` rejects unknown model names with ``-32602``
(``INVALID_PARAMS``) so the frontend can show a "model no longer
available" message without crashing.
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
# Backward-compatible constants
# ---------------------------------------------------------------------------
# These are kept for tests that import them directly. The actual model
# list is now dynamic from ProviderDAO.list_models(), but the
# built-in MiniMax provider seeds these same three models.

CANDIDATE_MODELS: tuple[str, ...] = (
    "MiniMax-M3",
    "MiniMax-M3-fast",
    "MiniMax-Code",
)

#: Legacy metadata — still used by test_model.py assertions.
MODEL_META: dict[str, dict[str, Any]] = {
    "MiniMax-M3": {
        "id": "MiniMax-M3",
        "name": "MiniMax-M3",
        "provider": "MiniMax",
        "context_window": 200_000,
        "supports_tools": True,
        "is_default": True,
    },
    "MiniMax-M3-fast": {
        "id": "MiniMax-M3-fast",
        "name": "MiniMax-M3-fast",
        "provider": "MiniMax",
        "context_window": 128_000,
        "supports_tools": True,
    },
    "MiniMax-Code": {
        "id": "MiniMax-Code",
        "name": "MiniMax-Code",
        "provider": "MiniMax",
        "context_window": 1_000_000,
        "supports_tools": True,
    },
}

def is_valid_model(name: str) -> bool:
    """Return True iff ``name`` is in the built-in candidate set.

    .. deprecated:: Use ``ProviderDAO.list_models()`` for dynamic lookup.
    """
    return isinstance(name, str) and name in CANDIDATE_MODELS

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

_DAO_ATTR = "_model_prefs_dao"
_LOCK_ATTR = "_model_prefs_dao_lock"
_PROV_DAO_ATTR = "_provider_dao_for_model"
_PROV_LOCK_ATTR = "_provider_dao_for_model_lock"

def register_model_handlers(
    server: Any,
    dao: Any = None,
    provider_dao: Any = None,
) -> None:
    """Register the ``model.*`` handlers on ``server``.

    Parameters
    ----------
    server:
        The :class:`~minimax_code.ipc.server.IPCServer` instance.
    dao:
        Optional pre-built :class:`~.ModelPrefsDAO`. When ``None``,
        the first ``model.*`` call opens the async database and
        constructs the DAO, then caches it on
        ``server._model_prefs_dao`` for reuse.
    provider_dao:
        Optional pre-built :class:`~.ProviderDAO`. When ``None``,
        the first ``model.list`` call opens the async database and
        constructs the DAO lazily.
    """

    if dao is not None:
        setattr(server, _DAO_ATTR, dao)
        setattr(server, _LOCK_ATTR, asyncio.Lock())
    if provider_dao is not None:
        setattr(server, _PROV_DAO_ATTR, provider_dao)
        setattr(server, _PROV_LOCK_ATTR, asyncio.Lock())

    async def _ensure_dao() -> Any:
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
            from ..storage.dao.model_prefs import ModelPrefsDAO

            if os.environ.get("MINIMAX_CODE_NO_DB") == "1":
                raise HandlerError(
                    STORAGE_ERROR,
                    "storage is disabled (MINIMAX_CODE_NO_DB=1); model handlers need a DB",
                )
            try:
                db = await ensure_db()
                if db is None:
                    raise RuntimeError("storage is unavailable")
            except Exception as exc:
                logger.exception("failed to open storage for model handlers")
                raise HandlerError(
                    STORAGE_ERROR,
                    f"failed to open storage: {exc}",
                ) from exc
            dao_obj = ModelPrefsDAO(db)
            setattr(server, _DAO_ATTR, dao_obj)
            return dao_obj

    async def _ensure_provider_dao() -> Any:
        """Return a cached ProviderDAO, creating one on first call.

        Reuses the DB from the prefs DAO if available to avoid opening
        a second connection.
        """
        existing = getattr(server, _PROV_DAO_ATTR, None)
        if existing is not None:
            return existing
        lock = getattr(server, _PROV_LOCK_ATTR, None)
        if lock is None:
            lock = asyncio.Lock()
            setattr(server, _PROV_LOCK_ATTR, lock)
        async with lock:
            existing = getattr(server, _PROV_DAO_ATTR, None)
            if existing is not None:
                return existing
            from ..app import ensure_db
            from ..storage.dao.providers import ProviderDAO

            if os.environ.get("MINIMAX_CODE_NO_DB") == "1":
                raise HandlerError(
                    STORAGE_ERROR,
                    "storage is disabled (MINIMAX_CODE_NO_DB=1)",
                )
            try:
                db = await ensure_db()
                if db is None:
                    raise RuntimeError("storage is unavailable")
            except Exception as exc:
                logger.exception("failed to open storage for provider dao")
                raise HandlerError(
                    STORAGE_ERROR,
                    f"failed to open storage: {exc}",
                ) from exc
            prov_dao = ProviderDAO(db)
            setattr(server, _PROV_DAO_ATTR, prov_dao)
            return prov_dao

    # ---- handlers ---------------------------------------------------------

    async def handle_model_list(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys=set())
            # Dynamic model list from all enabled providers.
            models: list[dict[str, Any]] = []
            try:
                prov_dao = await _ensure_provider_dao()
                models = await prov_dao.list_models()
            except HandlerError:
                pass  # DB not ready yet — return empty list.
            except Exception:
                logger.exception("provider_dao.list_models failed; returning []")
            # Best-effort: read current selection from DB.
            current: str | None = None
            try:
                dao_obj = await _ensure_dao()
                pref = await dao_obj.get_current()
                current = pref["model_id"] if isinstance(pref, dict) else pref
            except HandlerError:
                pass  # DB not ready yet — return list without current.
            await ctx.reply({"models": models, "current": current})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("model.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "model.list failed")

    async def handle_model_get_current(params: Any, ctx: Context) -> None:
        try:
            dao_obj = await _ensure_dao()
            check_params(params, expected_keys=set())
            pref = await dao_obj.get_current()
            model = pref["model_id"] if isinstance(pref, dict) else pref
            await ctx.reply({"model": model})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("model.get_current failed")
            await ctx.reply_error(INTERNAL_ERROR, "model.get_current failed")

    async def handle_model_set_current(params: Any, ctx: Context) -> None:
        try:
            dao_obj = await _ensure_dao()
            # Accept both "model" (internal/test) and "model_id" (frontend).
            check_params(params, expected_keys=set())
            model = str(
                params.get("model_id") or params.get("model") or ""
            ).strip()
            if not model:
                raise HandlerError(
                    INVALID_PARAMS,
                    "param 'model_id' must be a non-empty string",
                )
            # Optional provider_id — if supplied, store it too.
            provider_id = params.get("provider_id")
            if provider_id is not None:
                provider_id = str(provider_id).strip() or None

            # Validate model exists across all enabled providers.
            valid_models = set()
            try:
                prov_dao = await _ensure_provider_dao()
                for m in await prov_dao.list_models():
                    valid_models.add(m.get("id"))
            except HandlerError:
                pass  # DB unavailable — skip validation (best-effort).

            if valid_models and model not in valid_models:
                raise HandlerError(
                    INVALID_PARAMS,
                    f"unknown model {model!r}; valid options: {sorted(valid_models)}",
                    data={"valid": sorted(valid_models)},
                )

            await dao_obj.set_current(model, provider_id=provider_id)
            # Rebuild sub-agent LLM so the next agent.send_message uses
            # the updated model / provider config.
            try:
                from ..app import rebuild_subagent_llm
                await rebuild_subagent_llm()
            except Exception:
                logger.debug("rebuild_subagent_llm after set_current failed; continuing")
            await ctx.reply({"ok": True, "model": model, "current": model})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("model.set_current failed")
            await ctx.reply_error(INTERNAL_ERROR, "model.set_current failed")

    server.register("model.list", handle_model_list)
    server.register("model.get_current", handle_model_get_current)
    server.register("model.set_current", handle_model_set_current)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

__all__ = [
    "CANDIDATE_MODELS",
    "MODEL_META",
    "is_valid_model",
    "register_model_handlers",
]
