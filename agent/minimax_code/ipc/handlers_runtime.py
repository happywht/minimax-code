"""IPC handlers — ``runtime.*`` namespace (R12 crash recovery).

Read-only diagnostic surface for the boot-time crash-recovery pipeline.
The heavy lifting lives in :func:`minimax_code.app._run_crash_recovery`
(invoked once from :func:`_maybe_open_db`); these handlers just expose
its result so the frontend Settings page can surface "recovered N runs
after an unexpected shutdown".

Methods
-------
``runtime.recovery_status``
    Snapshot of the last boot's recovery: whether the previous run
    crashed, how many orphan runs were marked failed, and the run ids.
    Returns ``available:False`` before the first recovery has run.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import ValidationError

from .handler_utils import HandlerError
from .protocol import INTERNAL_ERROR
from .server import Context

logger = logging.getLogger(__name__)


async def _handle_runtime_recovery_status(params: Any, ctx: Context) -> None:
    try:
        from ..app import get_recovery_result

        result = get_recovery_result()
        if result is None:
            await ctx.reply(
                {
                    "available": False,
                    "clean_start": True,
                    "reason": "recovery has not run yet",
                }
            )
            return
        await ctx.reply({"available": True, **result})
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message)
    except Exception:
        logger.exception("runtime.recovery_status failed")
        await ctx.reply_error(INTERNAL_ERROR, "runtime.recovery_status failed")


async def _research_config(params: Any, ctx: Context, *, write: bool) -> None:
    from ..app import ensure_db
    from ..research_settings import get_config, set_config
    from .protocol import INVALID_PARAMS, ResearchConfigGetParams, ResearchConfigSetParams

    try:
        values = (ResearchConfigSetParams if write else ResearchConfigGetParams).model_validate(params)
        db = await ensure_db()
        if db is None:
            raise RuntimeError("Storage unavailable")
        result = await asyncio.to_thread(set_config, db.path.parent, values.search_url) if write else await asyncio.to_thread(get_config, db.path.parent)
        await ctx.reply(result)
    except (ValidationError, ValueError):
        await ctx.reply_error(INVALID_PARAMS, "Invalid or unavailable research settings")
    except Exception:
        logger.exception("research settings operation failed")
        await ctx.reply_error(INTERNAL_ERROR, "Research settings unavailable")


async def _handle_research_get(params: Any, ctx: Context) -> None:
    await _research_config(params, ctx, write=False)


async def _handle_research_set(params: Any, ctx: Context) -> None:
    await _research_config(params, ctx, write=True)


def register_runtime_handlers(server: Any) -> None:
    """Register all ``runtime.*`` JSON-RPC methods on *server*."""
    server.register("runtime.recovery_status", _handle_runtime_recovery_status)
    server.register("runtime.research_get", _handle_research_get)
    server.register("runtime.research_set", _handle_research_set)
    logger.debug("registered runtime.* handlers")


__all__ = ["register_runtime_handlers"]
