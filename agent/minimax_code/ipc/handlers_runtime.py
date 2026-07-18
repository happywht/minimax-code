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

import logging
from typing import Any

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


def register_runtime_handlers(server: Any) -> None:
    """Register all ``runtime.*`` JSON-RPC methods on *server*."""
    server.register("runtime.recovery_status", _handle_runtime_recovery_status)
    logger.debug("registered runtime.* handlers")


__all__ = ["register_runtime_handlers"]
