"""Built-in JSON-RPC handlers.

These are always registered on the IPC server and used for liveness
probes and graceful shutdown. The application-level methods
(``agent.*``, ``session.*``, …) are registered by the application
bootstrap in :mod:`minimax_code.app` (introduced in a later task).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from .protocol import Event, NOT_IMPLEMENTED, RPCError
from .server import Context

logger = logging.getLogger(__name__)

# Per-process state. Trivially in-memory; the storage task will
# replace this with SQLite-backed sessions.
_START_TIME = time.time()
_SESSIONS: dict[str, dict[str, Any]] = {}


async def handle_ping(params: Any, ctx: Context) -> None:
    """``ping`` → ``{"pong": <timestamp>, "uptime_s": <float>}``."""
    await ctx.reply(
        {
            "pong": time.time(),
            "uptime_s": time.time() - _START_TIME,
            "server": "minimax-code-agent",
        }
    )


async def handle_status(_params: Any, ctx: Context) -> None:
    """``status`` → liveness + per-environment flags."""
    import sys

    await ctx.reply(
        {
            "uptime_s": time.time() - _START_TIME,
            "python": sys.version.split()[0],
            "agent": "minimax-code-agent",
            "version": "0.1.0",
            "active_sessions": len(_SESSIONS),
        }
    )


async def handle_shutdown(_params: Any, ctx: Context) -> None:
    """``shutdown`` → reply then stop the server loop."""
    logger.info("shutdown requested")
    await ctx.reply({"ok": True})
    ctx.server.stop()


async def handle_agent_send_message(params: Any, ctx: Context) -> None:
    """Stub for ``agent.send_message``.

    Implemented as a hello-world for the skeleton milestone:
    streams a single ``agent.message_chunk`` event with a greeting,
    then a final event with ``done: true``.

    The full agent loop lives in :mod:`minimax_code.agent.core` and
    will replace this stub.
    """
    if not isinstance(params, dict):
        await ctx.reply_error(-32602, "params must be an object")
        return

    content = (params.get("content") or "").strip()
    session_id = params.get("session_id") or f"ses_{uuid.uuid4().hex[:8]}"

    if content.lower() in {"hello", "hello!", "hi", "hi!"}:
        text = f"Hello from Python agent! session={session_id}"
    elif content:
        text = f"Echo (skeleton): {content}  (session={session_id})"
    else:
        text = f"(empty message) session={session_id}"

    message_id = f"msg_{uuid.uuid4().hex[:8]}"

    # Stream the response in a few chunks to demonstrate the SSE-like flow.
    step = 16
    for i in range(0, len(text), step):
        delta = text[i : i + step]
        await ctx.emit(
            "agent.message_chunk",
            {
                "session_id": session_id,
                "message_id": message_id,
                "delta": delta,
                "done": False,
            },
        )
        # Tiny pause so the UI can actually render the streaming effect.
        import asyncio as _asyncio

        await _asyncio.sleep(0.05)

    # Final event signals end of stream.
    await ctx.emit(
        "agent.message_chunk",
        {
            "session_id": session_id,
            "message_id": message_id,
            "delta": "",
            "done": True,
        },
    )

    await ctx.reply(
        {
            "session_id": session_id,
            "message_id": message_id,
            "text": text,
        }
    )


__all__ = [
    "handle_ping",
    "handle_status",
    "handle_shutdown",
    "handle_agent_send_message",
]
