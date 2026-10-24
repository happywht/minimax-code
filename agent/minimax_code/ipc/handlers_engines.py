"""JSON-RPC handlers for the ``engine.*`` namespace (M4).

``engine.list``  — capability cards for every registered engine,
                   availability probed live (codex on PATH? version?).
``engine.status`` — one engine's card by id; unknown ids reply
                    INVALID_PARAMS. Unavailable engines carry a reason
                    so the UI can disable them explicitly (M4: never a
                    silent fallback).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..engines import get_engine, list_engines
from .handler_utils import HandlerError, check_params
from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)


def register_engine_handlers(server: Any) -> None:
    async def handle_engine_list(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys=set())
            engines = await list_engines()
            await ctx.reply({"engines": [e.to_dict() for e in engines],
                             "default": "native"})
        except Exception:  # pragma: no cover — defensive
            logger.exception("engine.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "engine.list failed")

    async def handle_engine_status(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"engine_id"})
            engine_id = str(params["engine_id"])
            engine = get_engine(engine_id)
            if engine is None:
                raise HandlerError(INVALID_PARAMS, f"unknown engine_id: {engine_id!r}")
            info = engine.info()
            if asyncio.iscoroutine(info):
                info = await info
            await ctx.reply({"engine": info.to_dict()})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("engine.status failed")
            await ctx.reply_error(INTERNAL_ERROR, "engine.status failed")

    server.register("engine.list", handle_engine_list)
    server.register("engine.status", handle_engine_status)


__all__ = ["register_engine_handlers"]
