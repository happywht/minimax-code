"""IPC handlers — ``telemetry.*`` namespace (R11).

Read-only access to the in-memory telemetry bus plus an administrative
clear endpoint. Unlike ``audit.*`` (which hits SQLite), these read the
process-wide :class:`TelemetryEngine` directly — sub-millisecond, no DB
round-trip, no DAO factory.

Methods
-------
``telemetry.recent``
    Up to ``limit`` most-recent events (optionally filtered by
    ``event_type`` / ``session_id``). Newest last, matching the ring
    buffer's insertion order.
``telemetry.metrics``
    Per-session counters + latency stats. Pass ``session_id`` for one
    session; omit for an all-sessions roll-up plus global severity tallies.
``telemetry.clear``
    Drop buffered events + per-session metrics (admin / test hygiene).
``telemetry.trace``
    Reconstruct one trace's span tree (R14). Given a ``trace_id``, returns
    the flat span payloads plus the parent→children forest built by
    :func:`minimax_code.telemetry.tracing.build_tree`, so a UI can paint a
    waterfall of where a turn's time went (LLM vs each tool).
"""

from __future__ import annotations

import logging
from typing import Any

from ..telemetry.tracing import build_tree
from .handler_utils import HandlerError
from .protocol import INTERNAL_ERROR
from .server import Context

logger = logging.getLogger(__name__)


def _engine() -> Any:
    """Return the process-wide TelemetryEngine, or ``None`` if disabled."""
    try:
        from ..app import ensure_telemetry_engine

        return ensure_telemetry_engine()
    except Exception:
        logger.debug("telemetry engine unavailable", exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


async def _handle_telemetry_recent(params: Any, ctx: Context) -> None:
    try:
        engine = _engine()
        if engine is None:
            await ctx.reply({"events": [], "total": 0, "enabled": False})
            return
        if not isinstance(params, dict):
            params = {}
        limit = int(params.get("limit", 100))
        event_type = params.get("event_type")
        session_id = params.get("session_id")
        events = engine.recent(limit=limit, event_type=event_type, session_id=session_id)
        await ctx.reply(
            {
                "events": events,
                "total": len(events),
                "enabled": True,
                "buffered": engine.buffered_count,
            }
        )
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message)
    except Exception:
        logger.exception("telemetry.recent failed")
        await ctx.reply_error(INTERNAL_ERROR, "telemetry.recent failed")


async def _handle_telemetry_metrics(params: Any, ctx: Context) -> None:
    try:
        engine = _engine()
        if engine is None:
            await ctx.reply({"enabled": False})
            return
        if not isinstance(params, dict):
            params = {}
        session_id = params.get("session_id")
        snapshot = engine.metrics(session_id=session_id)
        snapshot["enabled"] = True
        await ctx.reply(snapshot)
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message)
    except Exception:
        logger.exception("telemetry.metrics failed")
        await ctx.reply_error(INTERNAL_ERROR, "telemetry.metrics failed")


async def _handle_telemetry_clear(params: Any, ctx: Context) -> None:
    try:
        engine = _engine()
        if engine is None:
            await ctx.reply({"ok": True, "cleared": 0, "enabled": False})
            return
        buffered = engine.buffered_count
        engine.clear()
        logger.info("telemetry buffer cleared (%d events dropped)", buffered)
        await ctx.reply({"ok": True, "cleared": buffered, "enabled": True})
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message)
    except Exception:
        logger.exception("telemetry.clear failed")
        await ctx.reply_error(INTERNAL_ERROR, "telemetry.clear failed")


async def _handle_telemetry_trace(params: Any, ctx: Context) -> None:
    """Reconstruct one trace's span tree (R14).

    Params: ``{"trace_id": "<hex>"}``. Returns the flat span payloads
    plus the parent→children forest from :func:`build_tree`, so a client
    can render a waterfall of where a turn's wall-clock went.
    """
    try:
        engine = _engine()
        if engine is None:
            await ctx.reply(
                {"trace_id": None, "spans": [], "tree": [], "enabled": False}
            )
            return
        if not isinstance(params, dict):
            params = {}
        trace_id = params.get("trace_id")
        if not trace_id or not isinstance(trace_id, str):
            await ctx.reply_error(
                INTERNAL_ERROR, "telemetry.trace requires a non-empty 'trace_id'"
            )
            return
        spans = engine.trace_spans(trace_id)
        tree = build_tree(spans)
        await ctx.reply(
            {
                "trace_id": trace_id,
                "spans": spans,
                "tree": tree,
                "span_count": len(spans),
                "enabled": True,
            }
        )
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message)
    except Exception:
        logger.exception("telemetry.trace failed")
        await ctx.reply_error(INTERNAL_ERROR, "telemetry.trace failed")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_telemetry_handlers(server: Any) -> None:
    """Register all ``telemetry.*`` JSON-RPC methods on *server*."""
    server.register("telemetry.recent", _handle_telemetry_recent)
    server.register("telemetry.metrics", _handle_telemetry_metrics)
    server.register("telemetry.clear", _handle_telemetry_clear)
    server.register("telemetry.trace", _handle_telemetry_trace)
    logger.debug("registered telemetry.* handlers")


__all__ = ["register_telemetry_handlers"]
