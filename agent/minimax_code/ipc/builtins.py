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
    """End-to-end chat handler for ``agent.send_message``.

    This is the canonical user-message entry point. The handler:

    1. Validates ``params`` and mints a session id if one was not
       supplied.
    2. Ensures a ``sessions`` row exists (the ``messages`` table has
       a NOT-NULL FK on it) — done via the process-wide sessions
       DAO, falling back to lazy ``init_runtime`` if needed.
    3. Builds an :class:`~minimax_code.agent.AgentCore` with a
       :class:`~minimax_code.agent.MiniMaxClient` in **mock mode**
       (empty ``api_key`` → deterministic canned response, no
       network). The mock LLM emits the canned text in 16-char
       chunks; the core then emits a final ``done=True`` chunk.
    4. Wires ``AgentCore.on_chunk`` to push
       ``agent.message_chunk`` events to the frontend, one per
       streamed delta. The final chunk has ``done=True``.
    5. Persists the user message and the assistant message to the
       ``messages`` table via ``AgentCore.persist_message``, with a
       ``history_provider`` that replays prior turns for context.

    The reply envelope is::

        {
            "session_id": "...",
            "message_id": "...",
            "text": "<full assistant text>",
            "iterations": 1,
            "stub": true,            # true while MINIMAX_API_KEY is unset
            "tokens_in": 1,
            "tokens_out": <int>
        }
    """
    if not isinstance(params, dict):
        await ctx.reply_error(-32602, "params must be an object")
        return

    content = (params.get("content") or "").strip()
    session_id = str(params.get("session_id") or f"ses_{uuid.uuid4().hex[:8]}")
    if not content:
        await ctx.reply_error(-32602, "content must be a non-empty string")
        return

    message_id = f"msg_{uuid.uuid4().hex[:8]}"

    # Lazy imports — avoid pulling the agent core / storage layer in
    # for handlers that only need a ping / status response.
    from ..agent import AgentCore, AgentConfig, MiniMaxClient
    from ..app import get_sessions_dao, init_runtime
    from ..storage.dao.messages import MessagesDAO
    from ..storage.db import AsyncDatabase, default_database_path

    # 1. Ensure the sessions row exists (FK target for messages).
    #    Best-effort: if the sessions DAO is unavailable the
    #    messages INSERT will surface a clear FK error and the
    #    frontend can react. Mirrors the helper in
    #    :mod:`handlers_agents`.
    try:
        sess_dao = get_sessions_dao()
        if sess_dao is None:
            try:
                await init_runtime()
            except Exception:
                logger.debug("init_runtime failed; continuing without lazy session bootstrap")
            sess_dao = get_sessions_dao()
        if sess_dao is not None:
            existing = await sess_dao.get(session_id)
            if existing is None:
                await sess_dao.create(
                    id=session_id,
                    title=f"chat:{(content or '')[:32]}",
                    system_prompt="",
                    model=None,
                )
    except Exception:
        logger.exception("could not pre-create session %s; continuing", session_id)

    # 2. Open the async DB + messages DAO. The DB and the
    #    ``get_sessions_dao()`` singleton above share the same on-
    #    disk file but use independent ``aiosqlite`` connections —
    #    that's fine in WAL mode.
    try:
        db = AsyncDatabase(default_database_path())
        await db.connect()
        msg_dao = MessagesDAO(db)
    except Exception as exc:
        logger.exception("failed to open storage for chat")
        await ctx.reply_error(-32603, f"storage unavailable: {exc}")
        return

    async def _history(sid: str) -> list[dict[str, Any]]:
        try:
            rows = await msg_dao.list_for_session(sid, order_by="created_at ASC")
            out: list[dict[str, Any]] = []
            for r in rows:
                role = r.get("role") or "user"
                content_text = r.get("content") or ""
                msg: dict[str, Any] = {"role": role, "content": content_text}
                tc = r.get("tool_calls")
                if tc:
                    msg["tool_calls"] = tc
                tcid = r.get("tool_call_id")
                if tcid:
                    msg["tool_call_id"] = tcid
                out.append(msg)
            return out
        except Exception:
            logger.exception("history_provider(%s) failed; returning []", sid)
            return []

    async def _persist(sid: str, msg: dict[str, Any]) -> None:
        try:
            mid = f"msg_{uuid.uuid4().hex[:12]}"
            await msg_dao.create(
                id=mid,
                session_id=sid,
                role=str(msg.get("role", "user")),
                content=str(msg.get("content") or ""),
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
            )
        except Exception:
            # The agent loop treats persistence as best-effort; a
            # failure here must not abort the LLM stream the user
            # is already seeing. Log and continue.
            logger.exception("persist_message(%s, role=%s) failed", sid, msg.get("role"))

    # 3. Force mock mode by passing an empty API key. The
    #    MiniMaxClient constructor treats ``api_key=""`` (or unset)
    #    as mock mode and emits a deterministic canned response
    #    in 16-character chunks. This keeps the smoke test off
    #    the network.
    llm = MiniMaxClient(api_key="")

    # 3a. Resolve the permission store (lazily built by the
    #     ``permission.*`` handlers) and create a per-request
    #     :class:`PermissionGater` so the agent loop actually
    #     pauses for ``ask`` rules instead of silently
    #     default-allowing them.
    perm_store = None
    try:
        from ..ipc.handlers_permissions import _ensure_permission_store

        perm_store = await _ensure_permission_store(ctx.server)
    except Exception:
        logger.debug("permission store unavailable; running without gating")

    from ..perm_consent import PermissionGater

    gater = PermissionGater(emit=ctx.emit)
    # Stash the gater on the server so the ``permission.resolve``
    # handler (registered earlier in the same server) can find it.
    # The gater is request-scoped; the handler races on the
    # request_id-keyed map, so concurrent ``agent.send_message``
    # invocations stay isolated.
    setattr(ctx.server, "_permission_gater", gater)

    core = AgentCore(
        llm=llm,
        config=AgentConfig(),
        history_provider=_history,
        persist_message=_persist,
        permission_store=perm_store,
        permission_gater=gater,
    )

    async def _on_chunk(
        delta: str, done: bool, metadata: dict | None = None
    ) -> None:
        try:
            # The agent loop only attaches ``metadata`` to the
            # trailing ``done=True`` chunk per turn; every earlier
            # delta passes ``None``. ``ctx.emit`` with
            # ``metadata=`` merges it into the data dict when
            # present, so the wire format matches the v0.3.0
            # design (``data.metadata = {thinking_count, ...}``).
            await ctx.emit(
                "agent.message_chunk",
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "delta": delta,
                    "done": done,
                },
                metadata=metadata,
            )
        except Exception:
            logger.exception("on_chunk emit failed")

    core.on_chunk = _on_chunk

    # 4. Run the turn. The core will stream chunks (each becomes
    #    an ``agent.message_chunk`` event with ``done=False``),
    #    then emit a final ``done=True`` chunk and return.
    try:
        result = await core.run(session_id=session_id, user_message=content)
    except Exception as exc:
        logger.exception("agent.send_message: AgentCore.run failed")
        await ctx.reply_error(-32603, f"agent.send_message failed: {exc}")
        return

    await ctx.reply(
        {
            "session_id": session_id,
            "message_id": message_id,
            "text": result.final_text,
            "iterations": result.iterations,
            "stub": llm.mock,
            "tokens_in": result.usage.get("prompt_tokens", 0),
            "tokens_out": result.usage.get("completion_tokens", 0),
        }
    )


__all__ = [
    "handle_ping",
    "handle_status",
    "handle_shutdown",
    "handle_agent_send_message",
]
