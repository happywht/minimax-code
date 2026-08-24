"""Built-in JSON-RPC handlers.

These are always registered on the IPC server and used for liveness
probes and graceful shutdown. The application-level methods
(``agent.*``, ``session.*``, …) are registered by the application
bootstrap in :mod:`minimax_code.app` (introduced in a later task).
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from minimax_code import __version__

from .protocol import LLM_ERROR
from .server import Context

logger = logging.getLogger(__name__)

# Per-process state. Trivially in-memory; the storage task will
# replace this with SQLite-backed sessions.
_START_TIME = time.time()
_SESSIONS: dict[str, dict[str, Any]] = {}

# Active AgentCore instances keyed by session_id (main agent) or
# run_id (sub-agent), so that ``agent.cancel`` /
# ``agent.cancel_subagent`` can signal a running core to abort.
_ACTIVE_RUNS: dict[str, dict[str, Any]] = {}
# {key: {"core": AgentCore, "type": "main"|"subagent"}}


def _preview_value(value: Any, *, limit: int = 600) -> str:
    """Return a compact display preview for timeline rows."""
    try:
        import json

        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    text = text.replace("\r\n", "\n")
    return text if len(text) <= limit else text[:limit] + f"\n...(truncated, {len(text) - limit} chars)"


def _extract_sources(output: Any) -> list[dict[str, str | None]]:
    """Collect structured source annotations from a tool result payload.

    Scans dicts/lists recursively for ``source`` fields produced by
    codebase tools (``path#L1-10`` or ``path#L5``). Returns stable,
    de-duplicated annotations suitable for ``MessageMetadata.sources``.
    """
    sources: list[dict[str, str | None]] = []
    seen: set[tuple[str, str | None]] = set()

    def visit(obj: Any) -> None:
        if isinstance(obj, dict):
            src = obj.get("source")
            if isinstance(src, str) and src:
                if "#" in src:
                    file_path, line_range = src.split("#", 1)
                else:
                    file_path, line_range = src, None
                key = (file_path, line_range)
                if key not in seen:
                    seen.add(key)
                    sources.append({"file_path": file_path, "line_range": line_range})
            for value in obj.values():
                visit(value)
        elif isinstance(obj, list):
            for item in obj:
                visit(item)

    visit(output)
    return sources


class _RunRecorder:
    """Small adapter that mirrors AgentCore callbacks into run timeline rows."""

    def __init__(
        self,
        *,
        dao: Any,
        db: Any,
        emit: Callable[[str, dict[str, Any]], Awaitable[None]],
        session_id: str,
        title: str,
        assistant_message_id: str,
        mode: str = "chat",
        project_id: str | None = None,
    ) -> None:
        self.dao = dao
        self.db = db
        self.emit = emit
        self.session_id = session_id
        self.title = title
        self.assistant_message_id = assistant_message_id
        self.mode = mode
        self.project_id = project_id
        self.run_id = f"run_{uuid.uuid4().hex[:12]}"
        self._status_steps: dict[str, str] = {}
        self._tool_steps: dict[str, str] = {}

    async def create(self) -> dict[str, Any] | None:
        if self.dao is None:
            return None
        run = await self.dao.create_run(
            id=self.run_id,
            session_id=self.session_id,
            mode=self.mode,
            status="running",
            title=self.title,
            assistant_message_id=self.assistant_message_id,
        )
        await self.emit("run.created", {"run": run})
        return run

    async def status(self, status: str, detail: dict[str, Any]) -> None:
        if self.dao is None:
            return
        if status == "thinking":
            previous = self._status_steps.pop("thinking", None)
            if previous:
                completed = await self.dao.complete_step(previous, summary="Continued")
                await self.emit("run.step.completed", {"run_id": self.run_id, "step": completed})
            step = await self.dao.create_step(
                run_id=self.run_id,
                session_id=self.session_id,
                kind="thought",
                title="Thinking",
                summary=f"Iteration {detail.get('iteration', '')}".strip(),
                payload={"status": status, "detail": detail},
            )
            self._status_steps["thinking"] = step["id"]
            await self.emit("run.step.started", {"run_id": self.run_id, "step": step})
            return

        if status in {"calling_tool", "tool_running"}:
            return

        if status in {"done", "max_iterations"}:
            step_id = self._status_steps.pop("thinking", None)
            if step_id:
                step = await self.dao.complete_step(
                    step_id,
                    summary="Done thinking",
                    payload={"status": status, "detail": detail},
                )
                await self.emit("run.step.completed", {"run_id": self.run_id, "step": step})
            return

        if status in {"error", "permission_denied"}:
            step = await self.dao.create_step(
                run_id=self.run_id,
                session_id=self.session_id,
                kind="status",
                status="failed" if status == "error" else "completed",
                title=status.replace("_", " ").title(),
                summary=_preview_value(detail, limit=300),
                payload={"status": status, "detail": detail},
            )
            await self.emit("run.step.completed", {"run_id": self.run_id, "step": step})

    async def tool_call(self, call: dict[str, Any]) -> None:
        if self.dao is None:
            return
        tool_name = str(call.get("name") or "unknown")
        tool_call_id = str(call.get("id") or f"toolu_{uuid.uuid4().hex[:10]}")
        if tool_call_id in self._tool_steps:
            # Idempotency guard: a duplicate tool_call event for the same
            # tool_call_id must reuse the existing step — creating a second
            # one would orphan the first in "running" state forever on the
            # timeline (bit users as a spinner+red pair on exec_command).
            return
        args = call.get("args") or call.get("arguments") or {}
        step = await self.dao.create_step(
            run_id=self.run_id,
            session_id=self.session_id,
            kind="tool_call",
            title=tool_name,
            summary=_preview_value(args, limit=300),
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            payload={"args": args},
        )
        self._tool_steps[tool_call_id] = step["id"]
        await self.emit("run.step.started", {"run_id": self.run_id, "step": step})

    async def tool_result(self, call: dict[str, Any], result: Any) -> None:
        if self.dao is None:
            return
        tool_name = str(call.get("name") or "unknown")
        tool_call_id = str(call.get("id") or "")
        parent_id = self._tool_steps.get(tool_call_id)
        output = result.output if hasattr(result, "output") else str(result)
        error = result.error if hasattr(result, "error") else None
        success = bool(getattr(result, "success", error is None))
        if parent_id:
            call_step = await self.dao.complete_step(
                parent_id,
                status="completed" if success else "failed",
                summary="Completed" if success else str(error or "Tool failed"),
                error=error,
            )
            await self.emit("run.step.completed", {"run_id": self.run_id, "step": call_step})
        step = await self.dao.create_step(
            run_id=self.run_id,
            session_id=self.session_id,
            kind="observation",
            title=f"{tool_name} result",
            summary=_preview_value(error or output, limit=600),
            tool_call_id=tool_call_id or None,
            tool_name=tool_name,
            parent_id=parent_id,
            payload={
                "success": success,
                "output_preview": _preview_value(output, limit=1200),
                "metadata": getattr(result, "metadata", None),
            },
        )
        step = await self.dao.complete_step(
            step["id"],
            status="completed" if success else "failed",
            summary=_preview_value(error or output, limit=600),
            error=error,
        )
        await self.emit("run.step.completed", {"run_id": self.run_id, "step": step})

    async def complete(self, result: Any, *, blocks: int = 1) -> None:
        """Close the run timeline with the final block accounting.

        ``blocks`` counts the auto-continue blocks behind this one
        send-message call (1 when auto-continue is off). ``iterations``
        and ``compactions`` on ``result`` are already summed across
        blocks by ``_run_with_auto_continue``.
        """
        if self.dao is None:
            return
        step = await self.dao.create_step(
            run_id=self.run_id,
            session_id=self.session_id,
            kind="final",
            title="Final response",
            summary=_preview_value(getattr(result, "final_text", ""), limit=600),
            payload={
                "iterations": getattr(result, "iterations", None),
                "usage": getattr(result, "usage", None),
                "truncated": getattr(result, "truncated", False),
                "blocks": blocks,
                "compactions": getattr(result, "compactions", 0),
            },
        )
        step = await self.dao.complete_step(step["id"])
        await self.emit("run.step.completed", {"run_id": self.run_id, "step": step})
        run = await self.dao.update_run_status(
            self.run_id,
            status="cancelled" if getattr(result, "cancelled", False) else "completed",
            assistant_message_id=self.assistant_message_id,
            metadata={
                "iterations": getattr(result, "iterations", None),
                "usage": getattr(result, "usage", None),
                "truncated": getattr(result, "truncated", False),
                "blocks": blocks,
                "compactions": getattr(result, "compactions", 0),
            },
        )
        await self.emit("run.completed", {"run": run})
        await self._persist_memories(result)

    async def _persist_memories(self, result: Any) -> None:
        """Extract facts from the assistant reply and persist them as memories.

        Best-effort: failures are logged but never abort the user's run.
        """
        if self.db is None:
            return
        text = getattr(result, "final_text", "") or ""
        if not isinstance(text, str) or not text.strip():
            return
        try:
            from ..memory import MemoriesDAO, MemoryExtractor

            facts = MemoryExtractor().extract_facts(text)
            if not facts:
                return
            memories_dao = MemoriesDAO(self.db)
            memory_count = 0
            for fact in facts:
                await memories_dao.create(
                    content=fact["content"],
                    project_id=self.project_id,
                    session_id=self.session_id,
                    category="fact",
                    confidence=float(fact.get("confidence", 0.8)),
                    source="chat",
                )
                memory_count += 1
            # Echo the count back onto the assistant message metadata so the
            # UI can render a "N memories saved" chip.
            from ..storage.dao.messages import MessagesDAO

            msg_dao = MessagesDAO(self.db)
            existing = await msg_dao.get(self.assistant_message_id)
            metadata = existing.get("metadata") if existing else None
            if not isinstance(metadata, dict):
                metadata = {}
            metadata["memory_count"] = memory_count
            await msg_dao.update(self.assistant_message_id, metadata=metadata)
        except Exception:
            logger.debug("memory extraction failed; continuing", exc_info=True)

    async def fail(self, error: str) -> None:
        if self.dao is None:
            return
        run = await self.dao.update_run_status(self.run_id, status="failed", error=error)
        await self.emit("run.completed", {"run": run})


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
            "version": __version__,
            "active_sessions": len(_SESSIONS),
        }
    )


async def handle_shutdown(_params: Any, ctx: Context) -> None:
    """``shutdown`` → reply then stop the server loop."""
    logger.info("shutdown requested")
    await ctx.reply({"ok": True})
    ctx.server.stop()


async def _build_system_prompt_extra(
    *,
    session_id: str | None = None,
    project_id: str | None = None,
) -> str | None:
    """Build the ``system_prompt_extra`` payload for the current turn.

    Assembles context from the repo-map indexer (v0.4.0 Perception
    Engine) and, when available, relevant long-term memories for the
    current session/project (v0.11.0 Milestone 3). Always leads with the
    stale-note advisory (v1.1.1) so history pollution from old run-loop
    nudges is neutralised on every turn.
    """
    parts: list[str] = [
        # v1.1.1 — stale-note advisory. The run loop's ephemeral nudges
        # ([system note] …) are never persisted, but the model's
        # acknowledgements of them ARE ("收到，立刻收尾…", "budget
        # exhausted — wrapping up"). Later turns then imitated those
        # acknowledgements on every reply long after the note itself was
        # gone — the exact pollution observed on long multi-turn
        # sessions. The advisory is always true (the notes never persist)
        # and costs ~60 tokens per turn.
        "Stale-note advisory: earlier assistant messages in this "
        "conversation may contain phrases like \"wrapping up\", \"budget "
        "exhausted\", or \"no new work\" (e.g. 收尾 / 预算归零). Those "
        "acknowledged ephemeral system notes that applied only to the "
        "run which produced them — the notes are gone and their "
        "constraints no longer apply. Do not imitate those phrases: "
        "answer the current user message on its own merits, and only "
        "describe work as finished when it actually is.",
    ]
    # v1.3.0: workspace-root advisory. Injected only when this run is
    # anchored at a project root that differs from the process default
    # — sessions on the default root see no extra prompt noise.
    try:
        from ..workspace_ctx import current_root, env_or_cwd_root

        root = current_root()
        if root is not None and root != env_or_cwd_root():
            parts.append(
                f"Workspace root for this session: {root}\n"
                "All relative file paths resolve against this root. "
                "Absolute paths must stay inside it; access outside the "
                "root is rejected."
            )
    except Exception:  # pragma: no cover — defensive
        logger.debug("workspace-root advisory build failed", exc_info=True)
    try:
        from ..app import ensure_repo_map_indexer
        from ..workspace_ctx import current_root

        indexer = await ensure_repo_map_indexer(current_root())
        if indexer is not None:
            repo_map = await indexer.build_map()
            if repo_map:
                parts.append(repo_map)
    except Exception:
        logger.debug("repo-map generation failed; continuing without")

    try:
        from ..app import get_db
        from ..memory import MemoriesDAO, MemoryInjector

        db = get_db()
        if db is not None:
            memory_ctx = await MemoryInjector(MemoriesDAO(db)).build_context(
                project_id=project_id,
                session_id=session_id,
                query=None,
            )
            if memory_ctx:
                parts.append(memory_ctx)
    except Exception:
        logger.debug("memory context generation failed; continuing without")

    return "\n\n".join(parts) if parts else None


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

    raw_content = params.get("content")
    if isinstance(raw_content, list):
        content = raw_content  # multimodal — list of ContentPart dicts
    elif isinstance(raw_content, str):
        content = raw_content.strip()
    else:
        content = ""

    session_id = str(params.get("session_id") or f"ses_{uuid.uuid4().hex[:8]}")

    # Validate non-empty: for string, check .strip(); for list, check length
    if isinstance(content, str) and not content:
        await ctx.reply_error(-32602, "content must be a non-empty string")
        return
    if isinstance(content, list) and not content:
        await ctx.reply_error(-32602, "content must be a non-empty list")
        return

    # Extract a display-safe title string from the content.
    if isinstance(content, str):
        _title_hint = content[:32]
    elif isinstance(content, list):
        _title_hint = ""
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                _title_hint = part["text"][:32]
                break
        if not _title_hint:
            _title_hint = "[multimodal]"
    else:
        _title_hint = ""

    message_id = f"msg_{uuid.uuid4().hex[:8]}"

    # Lazy imports — avoid pulling the agent core / storage layer in
    # for handlers that only need a ping / status response.
    from ..agent import AgentConfig, AgentCore, MiniMaxClient
    from ..agent.tools import ToolRegistry, get_default_registry
    from ..agent.tools.codebase_find_symbol import FindSymbolCodebaseTool
    from ..agent.tools.codebase_navigate import NavigateCodebaseTool
    from ..agent.tools.codebase_search import SearchCodebaseTool
    from ..agent.tools.codebase_summarize import SummarizeCodebaseTool
    from ..app import (
        ensure_codebase_indexer,
        get_codebase_indexer,
        get_sessions_dao,
        init_runtime,
    )
    from ..codebase import CodebaseRetriever
    from ..models import context_window_for
    from ..storage.dao.messages import MessagesDAO
    from ..workspace_ctx import current_root

    # 1. Ensure the sessions row exists (FK target for messages).
    #    Best-effort: if the sessions DAO is unavailable the
    #    messages INSERT will surface a clear FK error and the
    #    frontend can react. Mirrors the helper in
    #    :mod:`handlers_agents`.
    project_id: str | None = None
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
                created = await sess_dao.create(
                    id=session_id,
                    title=f"chat:{_title_hint}",
                    system_prompt="",
                    model=None,
                )
                project_id = created.get("project_id")
            else:
                project_id = existing.get("project_id")
    except Exception:
        logger.exception("could not pre-create session %s; continuing", session_id)

    # 2. Resolve the process-wide database and messages DAO.
    #    Reuse the singleton opened by ``init_runtime`` so we never
    #    leak a second aiosqlite connection per request.
    try:
        await init_runtime()
        from ..app import get_db
        db = get_db()
        if db is None:
            await ctx.reply_error(-32603, "storage unavailable: database not initialised")
            return
        msg_dao = MessagesDAO(db)
        from ..storage.dao.runs import AgentRunsDAO
        runs_dao = AgentRunsDAO(db)
    except Exception:
        logger.exception("failed to open storage for chat")
        await ctx.reply_error(-32603, "storage unavailable")
        return

    # 2.5. v1.3.0 — publish this session's workspace root for the run.
    #     Everything below (prompt build, tool dispatch, sub-agents
    #     spawned via create_task) reads it through ``current_root()``;
    #     file tools anchor relative paths and enforce containment
    #     against it. Resolution never raises — a broken chain (missing
    #     project dir etc.) degrades to the process root.
    _root_token = None
    try:
        from ..workspace_ctx import resolve_root_for_session, set_current_root

        _root_token = set_current_root(await resolve_root_for_session(session_id))
    except Exception:  # pragma: no cover — defensive
        logger.exception("workspace root resolution failed; using process default")

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
            md = msg.get("metadata")
            md_dict = md if isinstance(md, dict) else None
            # Serialize list content (multimodal) as JSON string
            raw_cont = msg.get("content")
            if isinstance(raw_cont, list):
                import json as _json
                cont_str = _json.dumps(raw_cont)
            else:
                cont_str = str(raw_cont or "")
            # v1.1.3 — mirror the usage numbers onto the dedicated columns
            # too, so SUM(tokens_in) style session stats work; before this
            # only the metadata JSON carried them (and regular completions
            # carried neither).
            def _md_int(key: str) -> int:
                try:
                    return int((md_dict or {}).get(key) or 0)
                except (TypeError, ValueError):
                    return 0
            await msg_dao.create(
                id=mid,
                session_id=sid,
                role=str(msg.get("role", "user")),
                content=cont_str,
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
                tokens_in=_md_int("tokens_in"),
                tokens_out=_md_int("tokens_out"),
                metadata=md_dict,
            )
        except Exception:
            # The agent loop treats persistence as best-effort; a
            # failure here must not abort the LLM stream the user
            # is already seeing. Log and continue.
            logger.exception("persist_message(%s, role=%s) failed", sid, msg.get("role"))

    # 3. Reuse the process-wide LLM client singleton.  It is built
    #    once at process boot (via ``_rebuild_subagent_llm`` in
    #    ``app.py``) and kept in sync when the user changes model or
    #    provider.  Creating a new ``MiniMaxClient`` per request would
    #    open a fresh httpx connection pool and re-read the DB every
    #    time — wasteful and slow.
    from ..app import get_subagent_llm
    llm = get_subagent_llm()
    if llm is None:
        llm = MiniMaxClient()

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

    from ..perm_consent import PermissionGater, register_gater

    gater = PermissionGater(emit=ctx.emit)
    # Stash the gater on the server so the ``permission.resolve``
    # handler (registered earlier in the same server) can find it.
    # v1.2.2: registry-keyed by session — the legacy single
    # ``_permission_gater`` slot was overwritten by every new
    # ``agent.send_message`` call, so a concurrent run's consent
    # prompts could never be resolved and were denied by timeout.
    register_gater(ctx.server, session_id, gater)

    # Allow operators to tune or disable the per-chunk stall watchdog.
    # ``MINIMAX_STALL_TIMEOUT=180`` → 180 s; ``0`` → disable.
    _stall_env = os.environ.get("MINIMAX_STALL_TIMEOUT", "")
    stall_timeout = float(_stall_env) if _stall_env else 120.0

    # v1.1.0: auto-continue knobs (goal/loop-style long tasks). Off by
    # default — the manual continue button remains the resume path.
    auto_continue, auto_continue_max_blocks = _resolve_auto_continue()

    # Resolve the process-wide HookManager (R10). Built once via
    # ensure_hook_manager(), which also pours every enabled plugin's
    # hooks into the manager — so plugin lifecycle hooks are
    # agent-active on this run. None-safe and fail-open.
    hook_manager = None
    try:
        from ..app import ensure_hook_manager

        hook_manager = ensure_hook_manager()
    except Exception:
        logger.debug("hook manager unavailable; running without hooks")

    # Resolve the process-wide TelemetryEngine (R11). Optional everywhere;
    # None means telemetry is disabled for this run. Session lifecycle and
    # mirrored tool-dispatch events flow through it for in-memory observability.
    telemetry_engine = None
    try:
        from ..app import ensure_telemetry_engine

        telemetry_engine = ensure_telemetry_engine()
    except Exception:
        logger.debug("telemetry engine unavailable; running without telemetry")

    # R62: read the persisted reasoning-effort override so the main agent's
    # AgentConfig threads it into every LLM call (R55 wired config →
    # ``stream_chat`` → transport → wire). The override lives in the
    # process-wide singleton loaded by ``_rebuild_subagent_llm`` at boot
    # (and refreshed after every ``model.set_reasoning_effort`` /
    # ``model.set_current`` via ``rebuild_subagent_llm``). Symmetric to the
    # sub-agent path, which reads the same singleton in
    # ``SubAgentRuntime.build``. None-safe and fail-open: a missing override
    # (or a fresh boot before prefs loaded) leaves ``reasoning_effort=None``
    # → the model's own default effort (the pre-R62 behaviour).
    main_reasoning_effort: str | None = None
    try:
        from ..app import get_reasoning_effort_override

        main_reasoning_effort = get_reasoning_effort_override()
    except Exception:
        logger.debug("reasoning effort override unavailable; using model default")

    # Clone the default tool registry and inject the codebase RAG tool
    # when a workspace indexer is available (v0.11.0 Milestone 2).
    # v1.3.0: the indexer follows the run's workspace root (published by
    # the caller via workspace_ctx) so a project-rooted session searches
    # its own shard. Fail-open to the default root either way.
    registry = ToolRegistry()
    for tool in get_default_registry().list():
        registry.register(tool)
    try:
        indexer = ensure_codebase_indexer(current_root())
    except Exception:
        logger.debug("per-root codebase indexer unavailable", exc_info=True)
        indexer = None
    if indexer is None:
        indexer = get_codebase_indexer()
    if indexer is not None:
        try:
            retriever = CodebaseRetriever(
                indexer._store,
                indexer=indexer,
                embedder=indexer.embedder,
                root=indexer.root_key,
            )
            registry.register(SearchCodebaseTool(retriever))
            registry.register(SummarizeCodebaseTool(retriever))
            registry.register(FindSymbolCodebaseTool(retriever))
            registry.register(NavigateCodebaseTool(indexer.graph_index))
        except Exception:
            logger.exception("failed to register search_codebase tool")

    core = AgentCore(
        llm=llm,
        registry=registry,
        config=AgentConfig(
            system_prompt_extra=await _build_system_prompt_extra(
                session_id=session_id,
                project_id=project_id,
            ),
            stall_timeout=stall_timeout,
            reasoning_effort=main_reasoning_effort,
            # v1.1.0: feed the catalog's real context window so the
            # compaction gate in the run loop actually opens. Resolved
            # from the LLM singleton's current model (kept in sync by
            # ``rebuild_subagent_llm`` on every model.set_current).
            context_window=context_window_for(getattr(llm, "default_model", None)),
            # v1.1.0: auto-continue (goal/loop minimal form) — the loop
            # itself lives in _run_with_auto_continue below.
            auto_continue=auto_continue,
            auto_continue_max_blocks=auto_continue_max_blocks,
        ),
        history_provider=_history,
        persist_message=_persist,
        permission_store=perm_store,
        permission_gater=gater,
        hooks=hook_manager,
    )
    # Inject the telemetry engine (R11) so every tool dispatch mirrors into
    # the in-memory event bus. Optional — None when telemetry is disabled.
    core.telemetry_engine = telemetry_engine

    async def _emit_run_event(event: str, data: dict[str, Any]) -> None:
        try:
            await ctx.emit(event, data)
        except Exception:
            logger.exception("%s emit failed", event)

    current_assistant_message_id = message_id
    start_new_assistant_message = False

    recorder = _RunRecorder(
        dao=runs_dao,
        db=db,
        emit=_emit_run_event,
        session_id=session_id,
        title=_title_hint,
        assistant_message_id=message_id,
        project_id=project_id,
    )
    try:
        await recorder.create()
    except Exception:
        logger.exception("run recorder create failed; continuing without timeline")
        recorder.dao = None

    # v0.11.0: collect codebase tool source annotations across the turn
    # and attach them to the assistant message metadata on the final chunk.
    turn_sources: list[dict[str, str | None]] = []

    async def _on_chunk(
        delta: str, done: bool, metadata: dict | None = None
    ) -> None:
        nonlocal current_assistant_message_id, start_new_assistant_message
        try:
            if start_new_assistant_message:
                current_assistant_message_id = f"msg_{uuid.uuid4().hex[:8]}"
                start_new_assistant_message = False
            # The agent loop only attaches ``metadata`` to the
            # trailing ``done=True`` chunk per turn; every earlier
            # delta passes ``None``. ``ctx.emit`` with
            # ``metadata=`` merges it into the data dict when
            # present, so the wire format matches the v0.3.0
            # design (``data.metadata = {thinking_count, ...}``).
            # v0.11.0: merge any codebase sources collected from tool
            # results so the frontend can render a per-turn sources panel.
            if metadata is not None and turn_sources:
                metadata = {**metadata, "sources": turn_sources}
            await ctx.emit(
                "agent.message_chunk",
                {
                    "session_id": session_id,
                    "message_id": current_assistant_message_id,
                    "delta": delta,
                    "done": done,
                },
                metadata=metadata,
            )
        except Exception:
            logger.exception("on_chunk emit failed")

    # 3b. Register the core so ``agent.cancel`` can find it.
    _ACTIVE_RUNS[session_id] = {"core": core, "type": "main"}

    core.on_chunk = _on_chunk

    async def _on_status(status: str, detail: dict) -> None:
        """Push ``agent.status`` events so the frontend sees
        thinking / error / idle transitions."""
        try:
            await ctx.emit(
                "agent.status",
                {"session_id": session_id, "status": status, **detail},
            )
            await recorder.status(status, detail)
        except Exception:
            logger.exception("on_status emit failed")

    core.on_status = _on_status

    async def _on_tool_call(call: dict) -> None:
        """Push ``agent.tool_call`` events so the frontend can
        render tool-execution steps in the chat."""
        nonlocal start_new_assistant_message
        try:
            await ctx.emit(
                "agent.message_chunk",
                {
                    "session_id": session_id,
                    "message_id": current_assistant_message_id,
                    "delta": "",
                    "done": True,
                },
            )
            # ``call`` is the ``call_log`` dict built by
            # ``AgentCore._dispatch_tool`` — it uses the *flat*
            # normalised format {id, name, args, arguments, …}
            # produced by ``_extract_tool_calls``, NOT the nested
            # OpenAI format with a ``function`` wrapper.
            tool_name = call.get("name", "unknown")
            args = call.get("args") or call.get("arguments", {})
            tool_call_id = call.get("id", "")
            await ctx.emit(
                "agent.tool_call",
                {
                    "session_id": session_id,
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "args": args,
                    "message_id": current_assistant_message_id,
                },
            )
            start_new_assistant_message = True
            await recorder.tool_call(call)
        except Exception:
            logger.exception("on_tool_call emit failed")

    core.on_tool_call = _on_tool_call

    async def _on_ask_user(payload: dict) -> None:
        """Push ``agent.ask_user`` so the frontend renders an inline
        question card and can answer via ``agent.answer_user``."""
        try:
            await ctx.emit("agent.ask_user", payload)
        except Exception:
            logger.exception("on_ask_user emit failed")

    core.on_ask_user = _on_ask_user

    async def _on_tool_result(call: dict, result: Any) -> None:
        """Push ``agent.tool_result`` events with the tool output."""
        try:
            # Flat format — see _on_tool_call above.
            tool_name = call.get("name", "unknown")
            tool_call_id = call.get("id", "")
            await ctx.emit(
                "agent.tool_result",
                {
                    "session_id": session_id,
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "result": result.output if hasattr(result, "output") else str(result),
                    "error": result.error if hasattr(result, "error") else None,
                    "message_id": current_assistant_message_id,
                },
            )
            # v0.11.0: harvest source annotations from codebase tool results
            # so the final assistant message metadata can carry a sources list.
            try:
                output = result.output if hasattr(result, "output") else None
                turn_sources.extend(_extract_sources(output))
            except Exception:
                logger.debug("source extraction failed for %s", tool_name, exc_info=True)
            await recorder.tool_result(call, result)
        except Exception:
            logger.exception("on_tool_result emit failed")

    core.on_tool_result = _on_tool_result

    # 4. Run the turn. The core will stream chunks (each becomes
    #    an ``agent.message_chunk`` event with ``done=False``),
    #    then emit a final ``done=True`` chunk and return.
    # Fire session_start hooks (R10) so plugin lifecycle hooks activate
    # for this run. Notification-only; fail-open never blocks the turn.
    if hook_manager is not None:
        try:
            await hook_manager.fire_session_start(session_id)
        except Exception:
            logger.exception("session_start hooks failed; continuing")
    if telemetry_engine is not None:
        try:
            from ..telemetry import EventType, TelemetryEvent

            telemetry_engine.emit(
                TelemetryEvent(
                    type=EventType.SESSION_START,
                    session_id=session_id,
                    payload={"message_id": message_id} if message_id else {},
                )
            )
        except Exception:
            logger.debug("telemetry session_start emit failed", exc_info=True)
    try:
        # v1.1.0: auto-continue loop — block 1 plus up to
        # ``auto_continue_max_blocks - 1`` automatic continuations while
        # the budget keeps truncating. Stays inside this try so the core
        # remains registered in ``_ACTIVE_RUNS`` (user cancel works
        # between blocks) and session_end fires exactly once at the end.
        result, blocks = await _run_with_auto_continue(
            core, session_id=session_id, content=content
        )
    except Exception as exc:
        logger.exception("agent.send_message: AgentCore.run failed")
        await recorder.fail(str(exc))
        from ..agent.types import LLMStreamTimeout

        if isinstance(exc, LLMStreamTimeout):
            await ctx.reply_error(
                LLM_ERROR,
                str(exc),
            )
        else:
            await ctx.reply_error(-32603, "agent.send_message failed")
        return
    finally:
        _ACTIVE_RUNS.pop(session_id, None)
        # v1.3.0: unpublish the session root. Placed here so both the
        # happy path and every error path inside the run reset it.
        if _root_token is not None:
            try:
                from ..workspace_ctx import reset_current_root

                reset_current_root(_root_token)
            except Exception:  # pragma: no cover — defensive
                logger.debug("workspace root reset failed", exc_info=True)
            _root_token = None
        # v1.2.2: drop this run's consent gater from the server
        # registry so resolve stops seeing a dead gater.
        try:
            from ..perm_consent import unregister_gater

            unregister_gater(ctx.server, session_id)
        except Exception:  # pragma: no cover — defensive
            logger.debug("unregister_gater failed", exc_info=True)
        # Fire session_end hooks (R10) symmetrically — even on failure —
        # so plugins see the complete run lifecycle. Fail-open.
        if hook_manager is not None:
            try:
                await hook_manager.fire_session_end(session_id)
            except Exception:
                logger.exception("session_end hooks failed; continuing")
        # Mirror the lifecycle into telemetry (R11), symmetrically and
        # fail-open — session_end fires even on the exception path above.
        if telemetry_engine is not None:
            try:
                from ..telemetry import EventType, TelemetryEvent

                telemetry_engine.emit(
                    TelemetryEvent(
                        type=EventType.SESSION_END,
                        session_id=session_id,
                        payload={"message_id": message_id} if message_id else {},
                    )
                )
            except Exception:
                logger.debug("telemetry session_end emit failed", exc_info=True)

    await recorder.complete(result, blocks=blocks)

    await ctx.reply(
        {
            "session_id": session_id,
            "message_id": current_assistant_message_id,
            "run_id": recorder.run_id,
            "text": result.final_text,
            "iterations": result.iterations,
            "truncated": result.truncated,
            # v1.1.0: block accounting for auto-continue (1 when off) —
            # iterations/compactions above are already summed across
            # blocks by _run_with_auto_continue.
            "blocks": blocks,
            "compactions": result.compactions,
            "stub": llm.mock,
            "tokens_in": result.usage.get("prompt_tokens", 0),
            "tokens_out": result.usage.get("completion_tokens", 0),
        }
    )


async def handle_agent_cancel(params: Any, ctx: Context) -> None:
    """Cancel a running ``agent.send_message`` call by session id.

    Looks up the :class:`AgentCore` in :data:`_ACTIVE_RUNS` and
    calls :meth:`AgentCore.cancel` on it.  If no active core is found
    for the given ``session_id`` the handler returns a graceful
    ``{"ok": true, "note": "no active session"}`` so the caller does
    not need to distinguish between "already finished" and "never
    started".
    """
    if not isinstance(params, dict):
        await ctx.reply_error(-32602, "params must be an object")
        return

    session_id = params.get("session_id")
    if not session_id:
        await ctx.reply_error(-32602, "session_id is required")
        return

    entry = _ACTIVE_RUNS.pop(str(session_id), None)
    if entry is None or not entry.get("core"):
        await ctx.reply({"ok": True, "note": "no active session"})
        return

    entry["core"].cancel()
    await ctx.reply({"ok": True, "cancelled": str(session_id)})


#: Fixed continuation prompt for ``agent.continue_run`` (v1.1.0). Generic
#: across task domains — the actual task state lives in the conversation
#: history the send-message pipeline replays.
_CONTINUE_PROMPT = (
    "[continue] The previous turn stopped after reaching its iteration "
    "budget mid-task. Continue from where it stopped: review the "
    "conversation history above (including earlier tool results), skip "
    "work that is already done, finish the remaining steps, and then "
    "give the final answer."
)


def _resolve_auto_continue() -> tuple[bool, int]:
    """Resolve auto-continue knobs from the environment (v1.1.0).

    ``MINIMAX_AUTO_CONTINUE=1`` opts the main agent into automatic block
    continuation; ``MINIMAX_AUTO_CONTINUE_MAX_BLOCKS`` caps how many
    blocks one send-message may run (block 1 + N auto-continuations).
    Unparseable / out-of-range values fall back to the defaults with a
    warning — same fail-open posture as ``MINIMAX_STALL_TIMEOUT``.
    """
    enabled = os.environ.get("MINIMAX_AUTO_CONTINUE", "").strip() in {"1", "true", "yes"}
    max_blocks = 5
    raw = os.environ.get("MINIMAX_AUTO_CONTINUE_MAX_BLOCKS", "").strip()
    if raw:
        try:
            max_blocks = max(1, int(raw))
        except ValueError:
            logger.warning("MINIMAX_AUTO_CONTINUE_MAX_BLOCKS=%r is not an int; using 5", raw)
    return enabled, max_blocks


async def _run_with_auto_continue(core: Any, *, session_id: str, content: str) -> tuple[Any, int]:
    """Run the first block, then auto-continue while the budget truncates.

    Each block is a real ``AgentCore.run`` return — hooks fire, the
    truncated assistant message persists and streams, and a user cancel
    between blocks stops the loop (``run`` returns ``cancelled=True``).
    The returned result carries the *last* block's terminal state
    (final_text / truncated / cancelled) with ``iterations`` and
    ``compactions`` summed across blocks, so callers (run recorder,
    reply envelope) see one logical turn. Returns ``(merged_result,
    blocks)``.
    """
    from dataclasses import replace

    result = await core.run(session_id=session_id, user_message=content)
    blocks = 1
    total_iterations = result.iterations
    total_compactions = result.compactions
    while (
        getattr(core.config, "auto_continue", False)
        and result.truncated
        and not result.cancelled
        and blocks < core.config.auto_continue_max_blocks
    ):
        blocks += 1
        logger.info(
            "auto-continue: block %d/%d for session %s",
            blocks, core.config.auto_continue_max_blocks, session_id,
        )
        result = await core.run(session_id=session_id, user_message=_CONTINUE_PROMPT)
        total_iterations += result.iterations
        total_compactions += result.compactions
    if blocks > 1:
        result = replace(result, iterations=total_iterations, compactions=total_compactions)
    return result, blocks


async def handle_agent_continue_run(params: Any, ctx: Context) -> None:
    """``agent.continue_run`` — resume a budget-truncated turn (v1.1.0).

    Block-budget semantics: ``max_iterations`` caps a single *block* of
    work, not the whole task. This handler validates that the session's
    most recent run actually ended truncated (budget exhausted without a
    final answer), marks it as continued, then delegates to
    :func:`handle_agent_send_message` with a fixed continuation prompt —
    so the new block gets the full pipeline (history replay, tool
    dispatch, streaming chunks, run timeline, memory extraction) and the
    reply envelope is the standard send-message shape the UI already
    renders.

    Validation failures reply ``{"ok": False, "error": ...}`` instead of
    forwarding, so the caller can tell "nothing to continue" apart from
    a real run.
    """
    if not isinstance(params, dict):
        await ctx.reply_error(-32602, "params must be an object")
        return
    session_id = params.get("session_id")
    if not session_id:
        await ctx.reply_error(-32602, "session_id is required")
        return
    session_id = str(session_id)

    # The session's most recent run must have ended truncated. Degraded
    # storage (no db) → nothing recorded → nothing to continue.
    try:
        from ..app import get_db
        from ..storage.dao.runs import AgentRunsDAO

        db = get_db()
        if db is None:
            await ctx.reply({"ok": False, "error": "storage unavailable"})
            return
        dao = AgentRunsDAO(db)
        runs = await dao.list_runs(session_id=session_id, limit=1)
    except Exception as exc:
        await ctx.reply({"ok": False, "error": f"run lookup failed: {exc}"})
        return

    run = runs[0] if runs else None
    metadata = (run or {}).get("metadata") or {}
    if (
        not run
        or run.get("status") not in ("completed", "cancelled")
        or not metadata.get("truncated")
    ):
        await ctx.reply({"ok": False, "error": "no truncated run to continue"})
        return

    # Mark the old block as continued (telemetry/UI can spot chain
    # length). update_run_status uses COALESCE on completed_at, so the
    # original finish time survives; failures never block the resume.
    try:
        await dao.update_run_status(
            run["id"],
            status=run.get("status") or "completed",
            metadata={**metadata, "continued": True},
        )
    except Exception:
        logger.exception("marking run continued failed; continuing anyway")

    await handle_agent_send_message(
        {"session_id": session_id, "content": _CONTINUE_PROMPT}, ctx
    )


async def handle_agent_answer_user(params: Any, ctx: Context) -> None:
    """``agent.answer_user`` — answer a pending ``ask_user`` request (v1.1.1).

    The model pauses mid-turn with structured clarifying questions (the
    ``ask_user`` tool); the frontend renders them from the
    ``agent.ask_user`` broadcast and posts the user's selections back
    through this method. Resolving the future wakes the suspended tool
    call, whose result becomes the answers the model reads next
    iteration.

    ``answers`` is position-aligned with the questions: a list where
    each entry is a label string or a list of label strings
    (multi-select / Other free text).
    """
    if not isinstance(params, dict):
        await ctx.reply_error(-32602, "params must be an object")
        return
    request_id = params.get("request_id")
    if not request_id or not isinstance(request_id, str):
        await ctx.reply_error(-32602, "request_id is required")
        return
    answers = params.get("answers")
    if not isinstance(answers, list) or not answers:
        await ctx.reply_error(-32602, "answers must be a non-empty array")
        return

    from ..agent.core import _ASK_USER_ROUTES

    core = _ASK_USER_ROUTES.get(request_id)
    if core is None:
        await ctx.reply(
            {"ok": False, "error": "unknown or expired request_id (already answered, timed out, or cancelled)"}
        )
        return
    resolved = core.resolve_ask_user(request_id, answers)
    if not resolved:
        await ctx.reply({"ok": False, "error": f"request {request_id} is no longer pending"})
        return
    logger.info("ask_user %s answered by user", request_id)
    await ctx.reply({"ok": True, "request_id": request_id})


__all__ = [
    "handle_ping",
    "handle_status",
    "handle_shutdown",
    "handle_agent_send_message",
    "handle_agent_cancel",
    "handle_agent_continue_run",
    "handle_agent_answer_user",
]
