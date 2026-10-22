"""JSON-RPC handlers for the ``run.*`` timeline namespace."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from .handler_utils import HandlerError, check_params
from .protocol import INTERNAL_ERROR, INVALID_PARAMS, RunEventsParams
from .server import Context

logger = logging.getLogger(__name__)


async def _get_dao() -> Any:
    from ..app import get_db, init_runtime
    from ..storage.dao.runs import AgentRunsDAO

    db = get_db()
    if db is None:
        try:
            await init_runtime()
        except Exception:
            pass
        db = get_db()
    if db is None:
        raise HandlerError(INTERNAL_ERROR, "storage layer is not available")
    return AgentRunsDAO(db)


def register_run_handlers(server: Any) -> None:
    """Register run timeline read APIs."""

    async def handle_run_list(params: Any, ctx: Context) -> None:
        try:
            if params is None:
                params = {}
            check_params(params, expected_keys=set())
            dao = await _get_dao()
            session_id = params.get("session_id")
            status = params.get("status")
            # v1.4.0: optional mode filter — 'subagent' surfaces tool-path
            # sub-agent runs (see migration 028) without post-filtering.
            mode = params.get("mode")
            limit = params.get("limit", 20)
            offset = params.get("offset", 0)
            runs = await dao.list_runs(
                session_id=str(session_id) if session_id else None,
                status=str(status) if status else None,
                mode=str(mode) if mode else None,
                limit=int(limit) if limit else 20,
                offset=int(offset) if offset else 0,
                order_by="created_at DESC",
            )
            await ctx.reply({"runs": runs})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except (TypeError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, f"invalid run.list params: {exc}")
        except Exception:
            logger.exception("run.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "run.list failed")

    async def handle_run_steps(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"run_id"})
            run_id = str(params["run_id"])
            dao = await _get_dao()
            snapshot = await dao.snapshot(run_id)
            if snapshot is None:
                await ctx.reply_error(INVALID_PARAMS, f"unknown run_id: {run_id!r}")
                return
            await ctx.reply(snapshot)
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("run.steps failed")
            await ctx.reply_error(INTERNAL_ERROR, "run.steps failed")

    async def handle_run_events(params: Any, ctx: Context) -> None:
        try:
            request = RunEventsParams.model_validate(params)
            dao = await _get_dao()
            result = await dao.events(**request.model_dump())
            if result is None:
                raise HandlerError(INVALID_PARAMS, "unknown run_id")
            await ctx.reply(result)
        except ValidationError:
            await ctx.reply_error(INVALID_PARAMS, "invalid run.events params")
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("run.events failed")
            await ctx.reply_error(INTERNAL_ERROR, "run.events failed")

    server.register("run.list", handle_run_list)
    server.register("run.steps", handle_run_steps)
    server.register("run.events", handle_run_events)


__all__ = ["register_run_handlers"]
