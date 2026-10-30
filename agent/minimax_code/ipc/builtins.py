"""Built-in JSON-RPC handlers.

These are always registered on the IPC server and used for liveness
probes and graceful shutdown. The application-level methods
(``agent.*``, ``session.*``, …) are registered by the application
bootstrap in :mod:`minimax_code.app` (introduced in a later task).
"""

from __future__ import annotations

import asyncio
import copy
import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from minimax_code import __version__
from minimax_code.execution import capture_output, spawn_exec
from minimax_code.model_snapshot import ModelSnapshot
from minimax_code.runs import RunJob, get_run_service

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

_BASELINE_PROBE_TIMEOUT_S = 5.0


async def _baseline_git_workspace(workspace: str) -> bool:
    """Bounded advisory eligibility probe, with normal command permissions.

    The caller skips planning before entering this path. Managed spawn validates
    the accepted cwd, filters the environment, and owns descendant cleanup;
    bounded capture also retains cleanup before propagating cancellation.
    """
    proc = await spawn_exec(
        "git", "--no-pager", "-c", "core.fsmonitor=false", "rev-parse", "--git-dir",
        cwd=workspace, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    await capture_output(proc, timeout=_BASELINE_PROBE_TIMEOUT_S, limit=4096)
    return proc.returncode == 0


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
        model_snapshot: dict[str, Any] | None = None,
        run_id: str | None = None,
    ) -> None:
        self.dao = dao
        self.db = db
        self.emit = emit
        self.session_id = session_id
        self.title = title
        self.assistant_message_id = assistant_message_id
        self.mode = mode
        self.project_id = project_id
        self.model_snapshot = model_snapshot
        self.run_metadata: dict[str, Any] = {}
        self.admitted = run_id is not None
        self.run_id = run_id or f"run_{uuid.uuid4().hex[:12]}"
        self._status_steps: dict[str, str] = {}
        self._tool_steps: dict[str, str] = {}

    async def create(self) -> dict[str, Any] | None:
        if self.dao is None:
            return None
        if self.admitted:
            run = await self.dao.claim_run(self.run_id)
            if run is None:
                raise RuntimeError("Run is no longer queued; refusing duplicate execution")
            self.run_metadata = run.get("metadata") or {}
            await self.emit("run.updated", {"run": run})
            return run
        run = await self.dao.create_run(
            id=self.run_id,
            session_id=self.session_id,
            mode=self.mode,
            status="running",
            title=self.title,
            assistant_message_id=self.assistant_message_id,
            metadata={"model_snapshot": self.model_snapshot},
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
        call_step, step = await self.dao.record_tool_result(
            run_id=self.run_id,
            session_id=self.session_id,
            parent_id=parent_id,
            summary=_preview_value(error or output, limit=600),
            tool_call_id=tool_call_id or None,
            tool_name=tool_name,
            success=success,
            error=error,
            payload={
                "success": success,
                "output_preview": _preview_value(output, limit=1200),
                "metadata": getattr(result, "metadata", None),
            },
        )
        if call_step:
            await self.emit("run.step.completed", {"run_id": self.run_id, "step": call_step})
        await self.emit("run.step.completed", {"run_id": self.run_id, "step": step})

        try:
            from ..run_attachments import record_tool_attachments
            artifacts = await record_tool_attachments(self.db, self.run_id, self.session_id,
                                                       self.project_id, call, result)
        except Exception:
            # The original tool result is already durable. An auxiliary index
            # failure must not change it or assert that an association exists.
            logger.warning("Could not record task attachment association for %s; inspect project attachments",
                           self.run_id, exc_info=True)
            return
        for artifact in artifacts:
            try:
                await self.emit("run.step.completed", {"run_id": self.run_id, "step": artifact})
            except Exception:
                # The committed journal remains authoritative; reconnect can
                # retrieve it even when this notification was not delivered.
                logger.warning("Could not notify task attachment association for %s", self.run_id, exc_info=True)

    async def complete(self, result: Any, *, blocks: int = 1, receipt: dict[str, Any] | None = None,
                       verification: dict[str, Any] | None = None) -> None:
        """Close the run timeline with the final block accounting.

        ``blocks`` counts the auto-continue blocks behind this one
        send-message call (1 when auto-continue is off). ``iterations``
        and ``compactions`` on ``result`` are already summed across
        blocks by ``_run_with_auto_continue``. ``verification`` carries
        the acceptance evidence (tool failure counts) into the run row.
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
                "verification": verification,
            },
        )
        step = await self.dao.complete_step(step["id"])
        await self.emit("run.step.completed", {"run_id": self.run_id, "step": step})
        # M2 — a budget pause is a clean stop at a call boundary: the
        # run is neither completed nor failed, and run.resume continues
        # it without the review gate.
        cancelled = bool(getattr(result, "cancelled", False))
        budget_exhausted = bool(getattr(result, "budget_exhausted", False))
        status = "cancelled" if cancelled else (
            "budget_paused" if budget_exhausted else "completed")
        run = await self.dao.update_run_status(
            self.run_id,
            status=status,
            assistant_message_id=self.assistant_message_id,
            result=receipt,
            metadata={
                **self.run_metadata,
                "model_snapshot": self.model_snapshot,
                "iterations": getattr(result, "iterations", None),
                "usage": getattr(result, "usage", None),
                "truncated": getattr(result, "truncated", False),
                "blocks": blocks,
                "compactions": getattr(result, "compactions", 0),
                "budget_exhausted": budget_exhausted,
                "verification": verification,
            },
        )
        if self.run_metadata.get("agent_execution") is not None and (
            run is None or run["id"] != self.run_id or run["status"] != status
        ):
            from ..run_errors import RunPersistenceError
            raise RunPersistenceError("Agent terminal receipt could not be confirmed")
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
        run = await self.dao.terminate_run(self.run_id, status="failed", error=error)
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
    validate_command_override: str | None = None,
    workspace_advisory_override: str | None = None,
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
        from pathlib import Path

        from ..workspace_ctx import current_root, env_or_cwd_root

        root = Path(workspace_advisory_override) if workspace_advisory_override else current_root()
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

    # M3 — acceptance contract. When the project declares a validate
    # command, the model must run it (exec tool) before claiming a code
    # change is done and quote the real result; the pipeline also runs
    # it automatically on file-modifying runs, so honesty is enforced
    # twice — once by instruction, once by evidence.
    if project_id:
        try:
            from ..app import get_db as _get_db
            from ..storage.dao.projects import ProjectsDAO
            from ..verification import normalize_validate_command

            _db = _get_db()
            if _db is not None:
                proj_row = await ProjectsDAO(_db).get(str(project_id))
                _cmd = normalize_validate_command(validate_command_override if validate_command_override is not None else
                    (proj_row or {}).get("validate_command"))
                if _cmd:
                    parts.append(
                        "Project acceptance contract: this project's "
                        f"validation command is `{_cmd}`. Before you "
                        "report a code change as complete, run it with "
                        "the exec tool and quote the actual exit code "
                        "and output. Never claim success while it "
                        "fails; if it fails, fix the code or report "
                        "the failure honestly."
                    )
        except Exception:
            logger.debug("validate-command advisory build failed", exc_info=True)

    return "\n\n".join(parts) if parts else None


class _RunContext:
    """Capture a worker reply; transport waiters can detach or join independently."""

    def __init__(self, ctx: Context, job: RunJob):
        self.server, self._ctx, self.job = ctx.server, ctx, job

    async def emit(self, event, data, **kwargs):
        await _notify_run(self._ctx, event, data, **kwargs)

    async def reply(self, result):
        self.job.result = result

    async def reply_error(self, code, message, data=None):
        self.job.error = {"code": code, "message": message, "data": data}


async def _notify_run(ctx, event, data, **kwargs):
    try:
        await ctx.emit(event, data, **kwargs)
    except Exception:
        # Notifications are hints; disconnected observers cannot fail execution.
        # Durable state and receipts remain queryable through run.*.
        logger.debug("Run notification delivery failed: %s", event, exc_info=True)


async def _reply_for_run(ctx, dao, service, run, job, *, created, wait):
    if not wait:
        await ctx.reply({"run": run, "created": created})
        return
    if job is not None:
        try:
            await service.wait(job)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():
                raise  # only this RPC waiter detached
        except Exception:
            # The finalizer saves the same error receipt for the first waiter
            # and later retries. If persistence failed, report unconfirmed below.
            logger.debug("Run worker exited with an error", exc_info=True)
    receipt = await dao.result_payload(run["id"])
    if "agent_execution" in (run.get("metadata") or {}):
        current = await dao.get_run(run["id"])
        if current is None or current["status"] not in {"completed", "failed", "cancelled", "interrupted", "budget_paused"}:
            await ctx.reply_error(LLM_ERROR, "Agent terminal state is unconfirmed; inspect the retained run before recovery",
                                  {"run_id": run["id"], "requires_review": True})
            return
    if receipt is not None:
        if "error" in receipt:
            error = receipt["error"]
            await ctx.reply_error(error["code"], error["message"], error.get("data"))
        else:
            await ctx.reply(receipt)
        return
    current = await dao.get_run(run["id"])
    await ctx.reply_error(LLM_ERROR, "Run has no confirmed result; inspect its state before resuming",
                          {"run_id": run["id"], "status": current["status"] if current else None})


async def handle_agent_send_message(
    params: Any, ctx: Context, *, wait: bool = True, resumed_from_id: str | None = None,
    required_model: dict[str, Any] | None = None, required_workspace: str | None = None,
    required_permission_rules: list[dict[str, Any]] | None = None,
    continuation: bool = False, internal_schedule: dict[str, Any] | None = None,
    internal_skill: Any = None, skill_model: ModelSnapshot | None = None, skill_runtime: Any = None,
    internal_agent: Any = None,
) -> None:
    from ..execution import ExecutionPolicy, execution_scope
    from ..storage.dao.runs import AgentRunsDAO, RequestConflictError
    from .handler_utils import HandlerError
    from .host_session_gate import require_chat_session

    if not isinstance(params, dict):
        await ctx.reply_error(-32602, "params must be an object")
        return
    profile_key = "agent_execution" if internal_agent is not None else "skill_execution"
    if internal_agent is not None:
        internal_skill = internal_agent
    mode = params.get("mode", "chat")
    if mode not in {"chat", "plan", "execute"}:
        await ctx.reply_error(-32602, "mode must be chat, plan, or execute")
        return
    content = params.get("content")
    if not isinstance(content, (str, list)) or not content or (isinstance(content, str) and not content.strip()):
        await ctx.reply_error(-32602, "content must be a non-empty string or non-empty list")
        return
    key = params.get("idempotency_key")
    if key is not None and (not isinstance(key, str) or not key.strip() or len(key) > 128
                            or not isinstance(params.get("session_id"), str) or not params["session_id"]):
        await ctx.reply_error(-32602, "idempotency_key requires an explicit session_id and 1–128 non-blank characters")
        return
    session_id = str(params.get("session_id") or f"ses_{uuid.uuid4().hex[:8]}")
    request = {"session_id": session_id, "mode": mode, "content": copy.deepcopy(content)}
    restrict_network_tools = params.get("restrict_network_tools", False)
    if not isinstance(restrict_network_tools, bool):
        await ctx.reply_error(-32602, "restrict_network_tools must be a boolean")
        return
    if restrict_network_tools:
        request["restrict_network_tools"] = True
    capture = params.get("capture_file_changes", False)
    if not isinstance(capture, bool):
        await ctx.reply_error(-32602, "capture_file_changes must be a boolean")
        return
    if capture:
        request["capture_file_changes"] = True
    if internal_skill is not None:
        intent_key = "agent_intent" if internal_agent is not None else "skill_intent"
        request[intent_key] = copy.deepcopy(params[intent_key])
    service = get_run_service(ctx.server)
    try:
        from ..app import get_db, get_sessions_dao, init_runtime

        await init_runtime()
        db = get_db()
        if db is None:
            raise RuntimeError("storage unavailable: database not initialized")
        dao = AgentRunsDAO(db)
        await require_chat_session(session_id, internal_agent=internal_agent)
        scheduled = await db.fetchone("SELECT id FROM schedule_occurrences WHERE session_id=? LIMIT 1", (session_id,))
        if scheduled and internal_schedule is None:
            await ctx.reply_error(-32602, "Scheduled conversations require reviewed run.resume with their saved context")
            return
        agent_bound = await db.fetchone("SELECT run_id FROM agent_root_admissions WHERE execution_session_id=?", (session_id,))
        if agent_bound and internal_agent is None:
            await ctx.reply_error(-32602, "Agent execution sessions require their saved profile through run.resume or continue_run")
            return
        pinned = await db.fetchone("SELECT engine FROM sessions WHERE id=?", (session_id,))
        if pinned and pinned["engine"] != "native":
            await ctx.reply_error(-32602, "This session uses Codex; send through run.start or create a Native session")
            return
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
        return
    except Exception as exc:
        await ctx.reply_error(-32004, f"Cannot start task: {exc}")
        return
    try:
        existing = await dao.find_request(session_id, key, request) if key is not None else None
        if existing is not None:
            from ..runs import validate_recovery_retry

            retry_budget = (internal_schedule["context"]["budget_tokens"]
                            if internal_schedule is not None else params.get("budget_tokens"))
            validate_recovery_retry(existing, resumed_from_id, retry_budget)
    except RequestConflictError as exc:
        await ctx.reply_error(-32602, str(exc))
        return
    if existing is not None:
        job = await service.job_for_run(existing["id"])
        await _reply_for_run(ctx, dao, service, existing, job, created=False, wait=wait)
        return
    admitted_permissions = None
    if internal_schedule is None and internal_skill is None:
        try:
            from .handlers_permissions import _ensure_permission_store
            permission_store = await _ensure_permission_store(ctx.server)
            if permission_store is None:
                raise RuntimeError("Permission policy is unavailable")
            admitted_permissions = copy.deepcopy(required_permission_rules if required_permission_rules is not None
                                                 else permission_store.list_rules())
        except Exception as exc:
            await ctx.reply_error(-32004, f"Cannot capture task permissions: {exc}")
            return
    if isinstance(content, str):
        title = content.strip()[:32]
    else:
        title = next((p["text"][:32] for p in content if isinstance(p, dict)
                      and p.get("type") == "text" and isinstance(p.get("text"), str)), "[multimodal]")
    try:
        from ..workspace_ctx import resolve_root_for_session

        sessions = get_sessions_dao()
        if sessions is None:
            raise RuntimeError("Session storage is unavailable")
        if internal_agent is not None and not resumed_from_id:
            session = {"project_id": internal_agent.data["project_id"]}
            root = internal_agent.data["workspace"]
        else:
            session = await sessions.get(session_id)
            if session is None:
                try:
                    session = await sessions.create(id=session_id, title=f"chat:{title}", system_prompt="", model=None)
                except Exception:
                    session = await sessions.get(session_id)
                    if session is None:
                        raise
            root = str(await resolve_root_for_session(session_id))
        if required_workspace is not None and root != required_workspace:
            raise RuntimeError("Session workspace changed; restore its binding or start a new task")
        project_id = session.get("project_id")
        if internal_skill is not None:
            if project_id != internal_skill.data["project_id"] or root != internal_skill.data["workspace"]:
                raise ValueError("Skill session binding changed; start a new task")
        if internal_schedule is not None:
            saved = internal_schedule["context"]
            if project_id != saved["project_id"] or root != saved["workspace"]:
                raise ValueError("Scheduled session binding changed")
        # M3 — snapshot the project's acceptance command at admission so
        # a mid-run rebind cannot swap what this run will be validated
        # with. Unset / inbox (no project) means no automatic validation.
        validate_command = ""
        if project_id:
            from ..storage.dao.projects import ProjectsDAO

            proj_row = await ProjectsDAO(db).get(str(project_id))
            if proj_row is not None:
                from ..verification import normalize_validate_command

                validate_command = normalize_validate_command(proj_row.get("validate_command"))
    except Exception as exc:
        await ctx.reply_error(-32602, f"Cannot start task: {exc}")
        return

    if internal_schedule is not None:
        validate_command = internal_schedule["context"]["validate_command"]

    # Durable reservations enforce family/day ceilings at each actual send.
    from ..budget import daily_budget_limit, resolve_run_budget

    budget_override = params.get("budget_tokens")
    run_budget = (internal_schedule["context"]["budget_tokens"] if internal_schedule is not None
                  else budget_override if resumed_from_id is not None
                  else resolve_run_budget(budget_override))
    daily_limit = daily_budget_limit()
    daily_baseline = 0

    try:
        if internal_skill is not None:
            from ..skill_execution import _private_model
            if skill_runtime is None:
                skill_runtime = await init_runtime()
            if skill_model is None:
                internal_skill, skill_model = await internal_skill.restore(internal_skill.data, skill_runtime, db)
            # The original selected client is borrowed only long enough to
            # copy configuration. The admitted run owns its private client.
            model_snapshot = _private_model(skill_model)
            internal_skill.configure_offline_review(model_snapshot.client)
        elif internal_schedule is not None:
            from ..schedule_context import resolve_model
            model_snapshot = await resolve_model(db, internal_schedule["context"])
        else:
            model_snapshot = ModelSnapshot.capture()
        if internal_schedule is None and internal_skill is None and required_model is not None and model_snapshot.metadata() != required_model:
            raise RuntimeError("Select the original provider, model and reasoning effort before resuming")
    except Exception as exc:
        await ctx.reply_error(LLM_ERROR, f"Cannot start task: {exc}")
        return
    async def close_owned_model():
        if internal_schedule is not None or internal_skill is not None:
            try:
                await model_snapshot.client.close()
            except Exception:
                logger.exception("scheduled model client close failed")

    async def execute(job: RunJob) -> None:
        nonlocal internal_skill
        from ..budget_ledger import BudgetHandle, budget_scope
        from ..run_ownership import owned_runtime
        from ..schedule_context import RetainedChildren, scheduled_runtime
        async def check_dispatch():
            try:
                await require_chat_session(session_id, internal_agent=(
                    internal_skill if internal_agent is not None else None))
            except HandlerError as exc:
                job.error = {"code": exc.code, "message": exc.message, "data": exc.data}
                raise
            if internal_agent is None:
                # A queued request keeps its admitted project and workspace.
                # A different project can resolve to the same directory, so
                # comparing paths alone does not protect project ownership.
                current = await sessions.get(session_id)
                current_root = str(await resolve_root_for_session(session_id))
                latest = await sessions.get(session_id)
                if (not current or not latest or current.get("project_id") != job.project_id
                        or latest.get("project_id") != job.project_id
                        or (current.get("engine") or "native") != "native"
                        or (latest.get("engine") or "native") != "native"
                        or current_root != job.workspace):
                    raise ValueError("Session project, engine or workspace changed while queued; start a new task")

        await check_dispatch()
        job.budget_handle = await BudgetHandle.for_run(db, job.run_id, daily_limit=daily_limit)
        children = RetainedChildren()
        schedule_token = scheduled_runtime.set({"context":internal_schedule["context"],"children":children}) if internal_schedule is not None else None
        if internal_skill is not None:
            from ..skill_bindings import SkillBindings
            tool_bindings = internal_skill.data.get("skill_bindings")
            if tool_bindings is None:
                # B1 retains its original v1 profile and frozen instructions;
                # this only records provider provenance inherited by children.
                tool_bindings = SkillBindings.capture(skill_runtime,
                    [internal_skill.data["skill_id"]] if "skill_id" in internal_skill.data else []).data
        owner_token = owned_runtime.set({"context": internal_skill.data["model_snapshot"], "children": children,
            "skill_runtime": skill_runtime, "model_provider_fingerprint": internal_skill.data["model_provider_fingerprint"],
            "mock": internal_skill.data["mock"], "tool_bindings": tool_bindings,
            **({"profile_kind": "agent", "verified_models": internal_skill.data["verified_models"]} if internal_agent is not None else {})}) if internal_skill is not None else None
        from contextlib import ExitStack

        from ..process_context import required_target_scope
        from ..process_types import ExecutionTargetSpec
        process_scopes = ExitStack()
        try:
            if internal_agent is not None:
                target = internal_skill.data.get("execution_target")
                restricted = target is not None and target["mode"] == "podman"
                process_scopes.enter_context(required_target_scope(
                    ExecutionTargetSpec(**target) if restricted else None))
                internal_skill = await internal_skill.prepare(db, job.run_id)
                if restricted:
                    from ..storage.dao.process_executions import ProcessExecutionsDAO
                    binding = await ProcessExecutionsDAO(db).binding_for_run(job.run_id)
                    if binding is None or binding.owner is None:
                        raise ValueError("Prepared agent process authority is unavailable")
                    process_scopes.enter_context(required_target_scope(
                        ExecutionTargetSpec(**binding.target), binding.owner))
                owned_runtime.set({**owned_runtime.get(), "execution_target": target,
                    "run_service": service,
                    "execution_installation_id": internal_skill.data.get("execution_installation_id"),
                    "execution_origin": "manual"})
            if internal_skill is not None:
                # Validate revocations and dependency identity when the queued
                # worker actually obtains the session lease.
                from dataclasses import replace
                internal_skill = replace(internal_skill, tools=tuple(await internal_skill.validate(skill_runtime, db)))
                from ..run_ownership import capture_provider_identities
                owned_runtime.set({**owned_runtime.get(), "provider_identities":
                    capture_provider_identities(skill_runtime, owned_runtime.get()["tool_bindings"])})
            from ..orchestrator.subagent import SubAgentRuntime, run_children_scope, subagent_runtime_scope

            await check_dispatch()
            runtime = SubAgentRuntime(llm=model_snapshot.client,
                                      reasoning_effort=model_snapshot.reasoning_effort)
            if not job.workspace:
                raise RuntimeError("Accepted workspace is missing; rebind before starting a new task")
            job.verify_workspace()
            with execution_scope(ExecutionPolicy(workspace=Path(job.workspace), read_only=mode == "plan", restrict_network_tools=restrict_network_tools)), subagent_runtime_scope(runtime), budget_scope(job.budget_handle), run_children_scope(children):
                await _handle_agent_send_message(request, _RunContext(ctx, job), job=job,
                                                 model_snapshot=model_snapshot, internal_schedule=internal_schedule,
                                                 internal_skill=internal_skill, admitted_permissions=admitted_permissions)
        finally:
            try:
                if children is not None:
                    # The inner handler may fail or be cancelled before it
                    # creates a preliminary receipt. Retain descendant evidence
                    # for the service finalizer even on those early exits.
                    interrupted = None
                    try:
                        descendants = await children.drain(cancel=True)
                    except asyncio.CancelledError as exc:
                        interrupted = exc
                        # drain() has already waited for cleanup before raising;
                        # retrieve its completed evidence without losing the
                        # original cancellation.
                        descendants = await children.drain(cancel=True)
                    if descendants:
                        early_exit = job.result is None
                        if job.result is None:
                            job.result = {"session_id": session_id, "message_id": job.message_id,
                                          "run_id": job.run_id, "text": ""}
                        verification = job.result.get("verification") or {}
                        job.result["verification"] = {
                            **verification,
                            "children": descendants,
                            "requires_review": early_exit or bool(verification.get("requires_review"))
                            or any(child.get("requires_review") for child in descendants),
                        }
                    if interrupted is not None:
                        raise interrupted
            finally:
                process_scopes.close()
                if schedule_token is not None:
                    scheduled_runtime.reset(schedule_token)
                if owner_token is not None:
                    owned_runtime.reset(owner_token)
                from ..orchestrator.subagent import pop_subagent_emit
                from ..perm_consent import unregister_gater

                _ACTIVE_RUNS.pop(session_id, None)
                pop_subagent_emit(session_id)
                unregister_gater(ctx.server, session_id)

    async def finalize(job: RunJob, error: BaseException | None) -> None:
        if job.steer_controller is not None:
            await job.steer_controller.close()
            job.steer_controller = None
        if internal_skill is not None:
            from ..budget_ledger import owned_cleanup
            try:
                await owned_cleanup(close_owned_model())
            except asyncio.CancelledError as exc:
                error = error or exc
        else:
            await close_owned_model()
        # Each governed request retains its own cleanup. Ordinary async
        # descendants may still be running: only startup may recover a family.
        current = await dao.get_run(job.run_id)
        # A completed receipt is immutable. A preliminary budget pause can
        # still fail or be cancelled before its final evidence is settled.
        if current and current["status"] in {"completed", "failed", "cancelled", "interrupted"}:
            return
        if current and current["status"] == "budget_paused" and error is None and not job.cancel_requested:
            return
        cancelled = isinstance(error, asyncio.CancelledError) or job.cancel_requested
        if cancelled:
            receipt = {"session_id": session_id, "message_id": job.message_id,
                       "run_id": job.run_id, "text": "", **(job.result or {}), "cancelled": True}
            reason = "Cancelled by user or service shutdown"
        else:
            failure = job.error or {"code": -32603, "message": str(error or "Run ended without a confirmed result")}
            receipt, reason = {**(job.result or {}), "error": failure}, failure["message"]
        from ..verification import verification_evidence
        receipt["run_status"] = "cancelled" if cancelled else "failed"
        receipt.update(cancelled=cancelled, budget_paused=False)
        if getattr(job, "budget_handle", None) is not None:
            receipt["budget"] = await job.budget_handle.snapshot()
        receipt["verification"] = verification_evidence(status=receipt["run_status"],
            base=(job.result or {}).get("verification"), configured=bool(job.validate_command))
        if internal_skill is not None:
            receipt = internal_skill.receipt(receipt)
        run = await dao.terminate_run(job.run_id, status="cancelled" if cancelled else "failed",
                                      error=reason, result=receipt)
        await _notify_run(ctx, "run.completed", {"run": run})

    async def admit_owned(coro):
        if internal_skill is None:
            return await coro
        admission = asyncio.create_task(coro)
        cancelled = None
        while not admission.done():
            try:
                await asyncio.shield(admission)
            except asyncio.CancelledError as exc:
                cancelled = cancelled or exc
        accepted = admission.result()
        if cancelled is not None:
            # A created job now owns this client. A duplicate owns no worker,
            # so the detached waiter must close its unused private client.
            if not accepted[2]:
                from ..budget_ledger import owned_cleanup
                await owned_cleanup(close_owned_model())
            raise cancelled
        return accepted

    try:
        run, job, created = await admit_owned(service.admit(
            dao=dao, session_id=session_id, request=request, request_key=key, title=title,
            metadata={**({"schedule": internal_schedule} if internal_schedule is not None else {}),
                      **({profile_key: internal_skill.snapshot()} if internal_skill is not None else {}),
                      "model_snapshot": model_snapshot.metadata(), "workspace_snapshot": root,
                      "project_id": project_id,
                      "restrict_network_tools": restrict_network_tools,
                      **({"permission_rules_snapshot": admitted_permissions} if admitted_permissions is not None else {}),
                      "validate_command": validate_command,
                      "budget": {"limit_tokens": run_budget, "daily_limit": daily_limit,
                                 "daily_baseline": daily_baseline}},
            workspace=root, project_id=project_id,
            operation=execute, finalize=finalize, resumed_from_id=resumed_from_id,
            budget_tokens=run_budget, daily_baseline=daily_baseline,
            daily_budget_limit=daily_limit, validate_command=validate_command,
            continuation=continuation))
    except (RequestConflictError, ValueError) as exc:
        await close_owned_model()
        await ctx.reply_error(-32602, str(exc))
        return
    except Exception:
        await close_owned_model()
        logger.exception("durable run admission failed")
        await ctx.reply_error(-32004, "Cannot start task: request could not be durably admitted")
        return
    if not created:
        await close_owned_model()
    if created:
        await _notify_run(ctx, "run.created", {"run": run})
    await _reply_for_run(ctx, dao, service, run, job, created=created, wait=wait)


async def _handle_agent_send_message(
    params: Any, ctx: Context, *, job: RunJob, model_snapshot: ModelSnapshot,
    internal_schedule: dict[str, Any] | None = None,
    internal_skill: Any = None,
    admitted_permissions: list[dict[str, Any]] | None = None,
) -> None:
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

    message_id = job.message_id

    # Lazy imports — avoid pulling the agent core / storage layer in
    # for handlers that only need a ping / status response.
    from ..agent import AgentConfig, AgentCore
    from ..agent.tools import ToolRegistry, get_default_registry
    from ..agent.tools.codebase_find_symbol import FindSymbolCodebaseTool
    from ..agent.tools.codebase_navigate import NavigateCodebaseTool
    from ..agent.tools.codebase_search import SearchCodebaseTool
    from ..agent.tools.codebase_summarize import SummarizeCodebaseTool
    from ..app import (
        ensure_codebase_indexer,
        init_runtime,
    )
    from ..codebase import CodebaseRetriever
    from ..storage.dao.messages import MessagesDAO
    from ..workspace_ctx import current_root

    project_id = job.project_id

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
    #     against it. An invalid binding must stop before any model/tool call.
    _root_token = None
    try:
        from pathlib import Path

        from ..workspace_ctx import set_current_root

        root = Path(job.workspace or "")
        if not job.workspace or not root.is_dir() or root.resolve(strict=True) != root:
            raise RuntimeError("Accepted workspace is missing or has changed; rebind before starting a new task")
        _root_token = set_current_root(root)
    except Exception as exc:
        logger.warning("workspace root resolution failed: %s", exc)
        await ctx.reply_error(-32602, f"Cannot start task: {exc}")
        return

    # 2.6. v1.4.0 — publish this session as the *parent* for sub-agents
    #     spawned during the run, and route their ``agent.subagent_progress``
    #     events back through this connection. The tool path
    #     (``spawn_subagent``) has no other way to reach ``ctx.emit``;
    #     without the route the tool degrades to silent (fail-open).
    _parent_token = None
    try:
        from ..orchestrator.subagent import (
            register_subagent_emit,
            set_parent_session,
        )

        _parent_token = set_parent_session(session_id)
        register_subagent_emit(
            session_id,
            lambda event, payload: ctx.emit(event, payload),
        )
    except Exception:  # pragma: no cover — defensive
        _parent_token = None
        logger.debug("subagent parent-session publish failed", exc_info=True)

    async def _history(sid: str) -> list[dict[str, Any]]:
        from ..run_errors import RunPersistenceError

        try:
            from ..conversation import project_history

            rows = await msg_dao.list_for_session(sid, order_by="created_at ASC")
            return project_history(rows)
        except RunPersistenceError:
            raise
        except Exception as exc:
            raise RunPersistenceError("Conversation history could not be loaded; execution stopped") from exc

    async def _persist(sid: str, msg: dict[str, Any]) -> None:
        nonlocal start_new_assistant_message, assistant_saved
        try:
            role = str(msg.get("role", "user"))
            if role == "assistant":
                mid = _assistant_segment()
            else:
                mid = f"msg_{uuid.uuid4().hex}"
                if role == "user" and assistant_saved:
                    # Automatic continuation starts a new model response even
                    # when the previous block ended without another tool call.
                    start_new_assistant_message = True
            md = msg.get("metadata")
            md_dict = {**(md if isinstance(md, dict) else {}), "run_id": job.run_id}
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
                role=role,
                content=cont_str,
                tool_calls=msg.get("tool_calls"),
                tool_call_id=msg.get("tool_call_id"),
                tokens_in=_md_int("tokens_in"),
                tokens_out=_md_int("tokens_out"),
                metadata=md_dict,
                run_id=job.run_id,
            )
            if role == "assistant":
                assistant_saved = True
                recorder.assistant_message_id = mid
        except Exception as exc:
            from ..run_errors import RunPersistenceError

            raise RunPersistenceError("Conversation evidence could not be saved; inspect the run and workspace before resuming") from exc

    # The accepted request keeps this client even while waiting for its session.
    llm = model_snapshot.client

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
        raise RuntimeError("Execution permission policy is unavailable") from None
    if perm_store is None:
        raise RuntimeError("Execution permission policy is unavailable")

    from ..perm_consent import PermissionGater, register_gater

    async def scheduled_attention(kind: str, payload: dict, *, closed: bool = False) -> None:
        # These notifications are historical hints. The live wait remains the
        # authority and is checked again by the client before navigation.
        if internal_schedule is None or not payload.get("request_id"):
            return
        import hashlib
        import json
        from ..storage.dao._base import now_iso

        request_id = payload["request_id"]
        notification_id = "ntf_sched_wait_" + hashlib.sha256(
            f"{job.run_id}:{kind}:{request_id}".encode()
        ).hexdigest()[:32]
        try:
            entry = None
            async with db.transaction() as conn:
                async with conn.execute(
                    "SELECT o.id,o.job_id,o.run_id,o.session_id,j.name FROM schedule_occurrences o JOIN scheduled_jobs j ON j.id=o.job_id WHERE o.id=? AND o.job_id=? AND o.run_id=? AND o.session_id=? AND o.status='running'",
                    (internal_schedule["occurrence_id"], internal_schedule["job_id"], job.run_id, session_id),
                ) as cursor:
                    occurrence = await cursor.fetchone()
                if closed:
                    await conn.execute(
                        "UPDATE notifications SET title=?, body=?, read=1, read_at=? WHERE id=? AND source='schedule_attention'",
                        ("定时任务请求已结束", "授权或输入请求已解决或失效；请在运行历史查看当前状态。", now_iso(), notification_id),
                    )
                elif occurrence and payload.get("session_id") == session_id and payload.get("run_id") == job.run_id:
                    binding = json.dumps({"job_id": occurrence["job_id"], "occurrence_id": occurrence["id"],
                        "run_id": job.run_id, "session_id": session_id, "project_id": project_id,
                        "request_id": request_id, "kind": kind}, ensure_ascii=False)
                    cursor = await conn.execute(
                        "INSERT OR IGNORE INTO notifications (id,type,title,body,source,source_id,priority,read,created_at,read_at) VALUES (?,?,?,?,?,?,1,0,?,NULL)",
                        (notification_id, "permission" if kind == "permission" else "info",
                         "定时任务曾请求授权" if kind == "permission" else "定时任务曾请求输入",
                         f"{occurrence['name']} · 打开前将核对请求是否仍有效。", "schedule_attention", binding, now_iso()),
                    )
                    if not cursor.rowcount:
                        return
                else:
                    return
                async with conn.execute("SELECT * FROM notifications WHERE id=?", (notification_id,)) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        entry = dict(row)
                        entry["read"] = bool(entry["read"])
            if entry:
                ctx.server.notify({"event": "notification.read" if closed else "notification.new", "data": entry})
        except Exception:
            logger.exception("Scheduled attention notification failed")

    async def approval_emit(event, data):
        if (internal_schedule is not None or internal_skill is not None) and event == "permission.request":
            data = {**data, "approval_policy": "explicit_once"}
        await ctx.emit(event, data)
        if event in {"permission.request", "permission.closed"}:
            await scheduled_attention("permission", data, closed=event == "permission.closed")
    if internal_schedule is not None or internal_skill is not None:
        from ..schedule_context import FrozenPermissions
        if perm_store is None:
            raise RuntimeError("Saved execution permission policy is unavailable")
        saved_permissions = internal_skill.data["permission_rules"] if internal_skill is not None else internal_schedule["context"]["permission_rules"]
        perm_store = FrozenPermissions(saved_permissions, perm_store)
    elif admitted_permissions is not None:
        from ..schedule_context import FrozenPermissions
        if perm_store is None:
            raise RuntimeError("Saved execution permission policy is unavailable")
        perm_store = FrozenPermissions(admitted_permissions, perm_store, ask_unmatched=False)
    gater = PermissionGater(emit=approval_emit, run_id=job.run_id,
                            explicit_once=internal_schedule is not None or internal_skill is not None)
    if internal_schedule is not None:
        from ..schedule_context import scheduled_runtime
        scheduled_runtime.set({**(scheduled_runtime.get() or {}),"context":internal_schedule["context"],"permissions":perm_store,"gater":gater})
    if internal_skill is not None:
        from ..run_ownership import owned_runtime
        owned_runtime.set({**(owned_runtime.get() or {}), "permissions":perm_store, "gater":gater})
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

        hook_manager = ensure_hook_manager() if internal_schedule is None and internal_skill is None else None
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

    # Clone the default tool registry and inject the codebase RAG tool
    # when a workspace indexer is available (v0.11.0 Milestone 2).
    # v1.3.0: the indexer follows the run's workspace root (published by
    # the caller via workspace_ctx) so a project-rooted session searches
    # its own shard. Never substitute the default root when this fails:
    # its index may contain a different project's data.
    registry = ToolRegistry()
    for tool in get_default_registry().list():
        registry.register(tool)
    try:
        indexer = ensure_codebase_indexer(current_root())
    except Exception:
        logger.warning("per-root codebase indexer unavailable", exc_info=True)
        indexer = None
    codebase_available = False
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
            codebase_available = True
        except Exception:
            logger.exception("failed to register search_codebase tool")

    # Only ordinary chat gets the run-bound attachment tools. Scheduled and
    # skill/agent runs retain their frozen tool capabilities.
    if internal_skill is None and internal_schedule is None:
        from ..agent.tools.generate_report_docx import register_report_docx_tool
        from ..agent.tools.generate_table_xlsx import register_table_xlsx_tool
        from ..agent.tools.generate_xlsx_from_csv import register_csv_xlsx_tool
        from ..agent.tools.generate_xlsx_from_xlsx import register_xlsx_import_tool
        from ..agent.tools.join_spreadsheets_xlsx import register_join_spreadsheet_tool
        from ..agent.tools.project_attachments import register_project_attachment_tools
        from ..agent.tools.public_research import register_public_research_tools
        from ..agent.tools.research_sources import ResearchSources

        research_sources = ResearchSources(db, session_id, project_id, job.run_id)
        register_project_attachment_tools(registry, db, session_id, project_id)
        register_report_docx_tool(registry, db, session_id, project_id, research_sources)
        register_table_xlsx_tool(registry, db, session_id, project_id)
        register_csv_xlsx_tool(registry, db, session_id, project_id)
        register_xlsx_import_tool(registry, db, session_id, project_id)
        register_join_spreadsheet_tool(registry, db, session_id, project_id)
        register_public_research_tools(registry, research_sources)

    if internal_skill is not None:
        internal_skill.install(registry)
        from ..run_ownership import owned_runtime
        owned_runtime.set({**(owned_runtime.get() or {}), "registry":registry})
    system_prompt_extra = (
        internal_skill.data["system_prompt_extra"] if internal_skill is not None
        else await _build_system_prompt_extra(
            session_id=session_id,
            project_id=project_id,
            **({"validate_command_override": job.validate_command} if internal_schedule is not None else {}),
        )
    )
    if not codebase_available:
        system_prompt_extra = (system_prompt_extra or "") + (
            "\n\nThe codebase index for this workspace is unavailable. "
            "Do not assume another project's index applies. Use available workspace "
            "file tools to inspect the current project, and disclose the limitation "
            "when it affects the answer."
        )
    core = AgentCore(
        llm=llm,
        registry=registry,
        config=AgentConfig(
            system_prompt_extra=system_prompt_extra,
            **({"skill_instructions":internal_skill.data["instructions"],
                "max_iterations":internal_skill.data["max_iterations"],
                "temperature":internal_skill.data.get("temperature")} if internal_skill is not None else {}),
            stall_timeout=stall_timeout,
            reasoning_effort=model_snapshot.reasoning_effort,
            context_window=model_snapshot.context_window,
            model=model_snapshot.model,
            # v1.1.0: auto-continue (goal/loop minimal form) — the loop
            # itself lives in _run_with_auto_continue below.
            auto_continue=auto_continue if internal_skill is None else False,
            auto_continue_max_blocks=auto_continue_max_blocks,
            # M2 — run + daily token budgets resolved at admission.
            run_budget_tokens=job.budget_tokens,
            daily_budget_tokens=job.daily_budget_limit,
            daily_budget_baseline=job.daily_baseline,
        ),
        history_provider=_history,
        persist_message=_persist,
        permission_store=perm_store,
        permission_gater=gater,
        hooks=hook_manager,
    )
    # M2 — durable run row mirrors waiting states so "why is it idle"
    # survives a page reload. Writes go through the same DAO + events
    # channel as every other status change.
    async def _set_run_state(state: str) -> None:
        run = await runs_dao.update_run_status(job.run_id, status=state)
        if run is not None:
            await _emit_run_event("run.updated", {"run": run})

    core.run_state_hook = _set_run_state
    # Inject the telemetry engine (R11) so every tool dispatch mirrors into
    # the in-memory event bus. Optional — None when telemetry is disabled.
    core.telemetry_engine = telemetry_engine
    job.cancel_callback = core.cancel

    async def _emit_run_event(event: str, data: dict[str, Any]) -> None:
        try:
            await ctx.emit(event, data)
        except Exception:
            logger.exception("%s emit failed", event)

    current_assistant_message_id = message_id
    start_new_assistant_message = False
    assistant_saved = False

    def _assistant_segment() -> str:
        nonlocal current_assistant_message_id, start_new_assistant_message
        if start_new_assistant_message:
            current_assistant_message_id = f"msg_{uuid.uuid4().hex}"
            start_new_assistant_message = False
        job.message_id = current_assistant_message_id
        return current_assistant_message_id

    recorder = _RunRecorder(
        dao=runs_dao,
        db=db,
        emit=_emit_run_event,
        session_id=session_id,
        title=_title_hint,
        assistant_message_id=message_id,
        project_id=project_id,
        mode=params.get("mode", "chat"),
        model_snapshot=model_snapshot.metadata(),
        run_id=job.run_id,
    )
    job.record = recorder
    try:
        await recorder.create()
    except Exception:
        logger.exception("run recorder create failed; refusing unrecorded execution")
        await ctx.reply_error(-32004, "Cannot start task: run history could not be saved")
        return

    # M3 — automatic baseline checkpoint. Snapshot the workspace the
    # moment a writable run is claimed, before any tool executes. This is
    # recovery evidence, not an exact per-run undo guarantee. Uses ``git stash
    # create`` (a dangling ref — the working tree is untouched) plus an
    # untracked-file copy; failures are advisory and never block the run.
    # Planning must not spawn the Git probe or create snapshot artifacts.
    if params.get("mode", "chat") != "plan" and job.workspace:
        try:
            from ..storage.dao.checkpoints import CheckpointDAO
            from ..storage.db import ensure_data_dir
            from ..workspace.checkpoint import CheckpointManager

            # Only Git workspaces get a baseline: copying every untracked
            # file in an arbitrary directory could be unbounded.
            if not await _baseline_git_workspace(job.workspace):
                logger.debug("run baseline skipped: %s is not a git workspace", job.workspace)
            else:
                ckpt_root = ensure_data_dir() / "checkpoints"
                ckpt_root.mkdir(parents=True, exist_ok=True)
                mgr = CheckpointManager(ckpt_root)
                ckpt_id = f"ckpt_{uuid.uuid4().hex[:12]}"
                snapshot = await mgr.create(
                    cwd=job.workspace,
                    checkpoint_id=ckpt_id,
                    session_id=session_id,
                    label="run-baseline",
                    message=f"Automatic baseline before run {job.run_id}",
                )
                await CheckpointDAO(db).create(
                    session_id=session_id,
                    label="run-baseline",
                    message=f"Automatic baseline before run {job.run_id}",
                    git_stash_ref=snapshot.git_stash_ref,
                    branch=snapshot.branch,
                    tracked_files=snapshot.tracked_files,
                    untracked_files=snapshot.untracked_files,
                    has_untracked_snapshot=snapshot.has_untracked_snapshot,
                    checkpoint_id=ckpt_id,
                    created_at=snapshot.created_at,
                )
                recorder.run_metadata = {**recorder.run_metadata,
                                         "baseline_checkpoint": ckpt_id}
                await runs_dao.update_run_status(
                    job.run_id, status="running",
                    metadata={**recorder.run_metadata,
                              "model_snapshot": model_snapshot.metadata()},
                )
        except Exception:
            logger.warning("run baseline checkpoint failed; continuing without it", exc_info=True)

    # v0.11.0: collect codebase tool source annotations across the turn
    # and attach them to the assistant message metadata on the final chunk.
    turn_sources: list[dict[str, str | None]] = []

    async def _on_chunk(
        delta: str, done: bool, metadata: dict | None = None
    ) -> None:
        try:
            _assistant_segment()
            # Usage arrives on the trailing chunk. All partial chunks carry
            # run identity so interrupted output can be reconciled with history
            # without inventing a completed/persisted message or zero usage.
            # v0.11.0: merge any codebase sources collected from tool
            # results so the frontend can render a per-turn sources panel.
            if metadata is not None and turn_sources:
                metadata = {**metadata, "sources": turn_sources}
            metadata = {**(metadata or {}), "run_id": job.run_id}
            seq = job.live_stream.append(current_assistant_message_id, delta, done)
            await ctx.emit(
                "agent.message_chunk",
                {
                    "session_id": session_id,
                    "run_id": job.run_id,
                    "message_id": current_assistant_message_id,
                    "delta": delta,
                    "done": done,
                    **({"stream_seq": seq} if seq is not None else {"stream_unavailable": True,
                       "stream_event_seq": job.live_stream.event_seq}),
                },
                metadata=metadata,
            )
        except Exception:
            logger.exception("on_chunk emit failed")

    # 3b. Register the core so ``agent.cancel`` can find it.
    _ACTIVE_RUNS[session_id] = {"core": core, "type": "main"}
    from ..run_steer import SteerController
    steer = SteerController(job, _emit_run_event)
    job.steer_controller = steer
    core.on_safe_interjections = steer.drain

    core.on_chunk = _on_chunk

    async def _on_status(status: str, detail: dict) -> None:
        """Push ``agent.status`` events so the frontend sees
        thinking / error / idle transitions."""
        try:
            await ctx.emit(
                "agent.status",
                {**detail, "session_id": session_id, "status": status, "run_id": job.run_id},
            )
            await recorder.status(status, detail)
        except Exception as exc:
            from ..run_errors import RunPersistenceError

            raise RunPersistenceError("Run status could not be saved; execution stopped") from exc

    core.on_status = _on_status

    async def _on_tool_call(call: dict) -> None:
        """Push ``agent.tool_call`` events so the frontend can
        render tool-execution steps in the chat."""
        nonlocal start_new_assistant_message
        try:
            seq = job.live_stream.append(current_assistant_message_id, "", True)
            await ctx.emit(
                "agent.message_chunk",
                {
                    "session_id": session_id,
                    "run_id": job.run_id,
                    "message_id": current_assistant_message_id,
                    "delta": "",
                    "done": True,
                    **({"stream_seq": seq} if seq is not None else {"stream_unavailable": True,
                       "stream_event_seq": job.live_stream.event_seq}),
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
            await recorder.tool_call(call)
            await ctx.emit(
                "agent.tool_call",
                {
                    "session_id": session_id,
                    "run_id": job.run_id,
                    "tool_call_id": tool_call_id,
                    "name": tool_name,
                    "args": args,
                    "message_id": current_assistant_message_id,
                },
            )
            start_new_assistant_message = True
        except Exception as exc:
            from ..run_errors import RunPersistenceError

            raise RunPersistenceError("Tool intent could not be saved; execution stopped before dispatch") from exc

    core.on_tool_call = _on_tool_call

    def _bind_ask_user(question_core, question_session, source=None):
        if not isinstance(question_session, str) or not question_session:
            raise ValueError("Question session is unavailable")

        async def _on_ask_user(payload: dict) -> None:
            """Publish the live question owned by this core and run."""
            if payload.get("session_id") != question_session:
                raise ValueError("Question session changed")
            event = {**payload, "session_id": session_id, "run_id": job.run_id,
                     **({"agent_name": source, "child_session_id": question_session} if source else {})}
            deadline = question_core._ask_user_deadlines[payload["request_id"]]
            event["remaining_ms"] = max(0, int((deadline - asyncio.get_running_loop().time()) * 1000))
            future = question_core._pending_ask_user.get(payload["request_id"])
            registry = getattr(ctx.server, "_live_ask_users", None)
            if registry is None:
                registry = ctx.server._live_ask_users = {}
            registry[payload["request_id"]] = (event, future, deadline)
            await ctx.emit("agent.ask_user", event)
            await scheduled_attention("question", event)

        async def _on_ask_user_closed(payload: dict) -> None:
            registry = getattr(ctx.server, "_live_ask_users", {})
            registry.pop(payload["request_id"], None)
            event = {**payload, "session_id": session_id, "run_id": job.run_id}
            await ctx.emit("agent.ask_user_closed", event)
            await scheduled_attention("question", event, closed=True)

        question_core.on_ask_user = _on_ask_user
        question_core.on_ask_user_closed = _on_ask_user_closed

    _bind_ask_user(core, session_id)
    from ..orchestrator.subagent import get_run_subagent_runtime
    child_runtime = get_run_subagent_runtime()
    if child_runtime is not None:
        child_runtime.bind_interaction = _bind_ask_user

    async def _on_tool_result(call: dict, result: Any) -> None:
        """Push ``agent.tool_result`` events with the tool output."""
        try:
            # Flat format — see _on_tool_call above.
            tool_name = call.get("name", "unknown")
            tool_call_id = call.get("id", "")
            # Commit the result before notifying clients; a disconnected client
            # can then recover the same outcome from the durable journal.
            await recorder.tool_result(call, result)
            await ctx.emit(
                "agent.tool_result",
                {
                    "session_id": session_id,
                    "run_id": job.run_id,
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
        except Exception as exc:
            from ..run_errors import RunPersistenceError

            raise RunPersistenceError("Tool result could not be saved; inspect its effects before resuming") from exc

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
    descendants = []
    from ..orchestrator.subagent import current_run_children
    children = current_run_children()
    child_drain_complete = False
    try:
        # v1.1.0: auto-continue loop — block 1 plus up to
        # ``auto_continue_max_blocks - 1`` automatic continuations while
        # the budget keeps truncating. Stays inside this try so the core
        # remains registered in ``_ACTIVE_RUNS`` (user cancel works
        # between blocks) and session_end fires exactly once at the end.
        from ..orchestrator.subagent import subagent_permission_scope
        with subagent_permission_scope(perm_store, gater, session_id):
            result, blocks = await _run_with_auto_continue(
                core, session_id=session_id, content=content
            )
        if children is not None and children.tasks:
            from ..verification import verification_evidence
            pending_proof = verification_evidence(status="running", base={
                "requires_review": True,
                "children_pending": [run_id for task, run_id in children.tasks.items() if not task.done()],
            })
            job.result = {"session_id":session_id,"message_id":current_assistant_message_id,
                "run_id":job.run_id,"text":result.final_text,"run_status":"running",
                "tokens_in":result.usage.get("prompt_tokens",0),"tokens_out":result.usage.get("completion_tokens",0),
                "verification":pending_proof}
            if internal_skill is not None:
                job.result = internal_skill.receipt(job.result, tool_calls=getattr(result, "tool_calls", None) or [])
            current_run = await runs_dao.get_run(job.run_id)
            saved_preliminary = await runs_dao.update_run_status(job.run_id,status=current_run["status"],result=job.result,
                metadata={**(current_run.get("metadata") or {}),"verification":pending_proof})
            if internal_skill is not None and saved_preliminary is None:
                from ..run_errors import RunPersistenceError
                raise RunPersistenceError("Owned preliminary receipt could not be confirmed")
        if children is not None:
            stop_children = bool(result.cancelled or result.truncated or getattr(result, "budget_exhausted", False))
            if stop_children:
                descendants = await children.drain(cancel=True)
            else:
                # Bound the wait even when a child is queued or its own timeout
                # is disabled. Cancellation still waits for owned cleanup.
                try:
                    descendants = await asyncio.wait_for(children.drain(), timeout=600)
                except TimeoutError:
                    descendants = await children.drain(cancel=True)
        child_drain_complete = True
    except Exception as exc:
        logger.exception("agent.send_message: AgentCore.run failed")
        from ..agent.types import LLMStreamTimeout
        from ..run_errors import RunPersistenceError

        if isinstance(exc, RunPersistenceError):
            await ctx.reply_error(-32004, str(exc), {"run_id": job.run_id, "requires_review": True})
        elif isinstance(exc, LLMStreamTimeout):
            await ctx.reply_error(
                LLM_ERROR,
                str(exc),
            )
        else:
            await ctx.reply_error(-32603, "agent.send_message failed")
        return
    finally:
        await steer.close()
        job.steer_controller = None
        if children is not None and not child_drain_complete:
            descendants = await children.drain(cancel=True)
            if job.result is not None:
                job.result["verification"] = {**job.result.get("verification",{}),"children":descendants,"requires_review":True}
        _ACTIVE_RUNS.pop(session_id, None)
        # v1.4.0: unpublish the parent-session context and drop this
        # run's event route so a later run with the same session id
        # starts from a clean slate (same layer as the root reset).
        if _parent_token is not None:
            try:
                from ..orchestrator.subagent import (
                    pop_subagent_emit,
                    reset_parent_session,
                )

                pop_subagent_emit(session_id)
                reset_parent_session(_parent_token)
            except Exception:  # pragma: no cover — defensive
                logger.debug("subagent parent-session unpublish failed", exc_info=True)
            _parent_token = None
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
        if hook_manager is not None and not job.cancel_requested and not core.cancelled:
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

    # M2 — acceptance evidence. A run's verbal "done" is not proof: any
    # failed tool call is surfaced here so a completion with failed
    # commands can be told apart from a verified one (运行结束 ≠ 验收通过).
    # Defensive access: stub cores in tests may omit the log fields.
    tool_calls_log = getattr(result, "tool_calls", None) or []
    tool_results_log = getattr(result, "tool_results", None) or []
    tool_failures = [
        {"tool": str(call.get("name") or "unknown"), "error": str(getattr(res, "error", None) or "")[:300]}
        for call, res in zip(tool_calls_log, tool_results_log, strict=True)
        if not getattr(res, "success", True)
    ]
    verification_base = {
        "tool_calls": len(tool_calls_log),
        "failed_tool_calls": len(tool_failures),
        "requires_review": bool(tool_failures) or bool(result.truncated) or any(child["requires_review"] for child in descendants),
        **({"children": descendants} if descendants else {}),
        "failures": tool_failures[:5],
    }

    from ..verification import verification_evidence, verify_native_result
    execution_status = "cancelled" if getattr(result, "cancelled", False) else (
        "budget_paused" if getattr(result, "budget_exhausted", False) else "completed")
    verification = verification_evidence(status=execution_status, mode=params.get("mode", "chat"),
        configured=bool(job.validate_command), truncated=bool(result.truncated), base=verification_base)
    job.result = {
        "session_id": session_id,
        "message_id": current_assistant_message_id,
        "run_id": recorder.run_id,
        "text": result.final_text,
        "iterations": result.iterations,
        "truncated": result.truncated,
        "cancelled": bool(getattr(result, "cancelled", False)),
        # M2 — the caller can distinguish a budget pause from a normal
        # completion without decoding the run status enum.
        "budget_paused": bool(getattr(result, "budget_exhausted", False)),
        "budget": (await job.budget_handle.snapshot()) if getattr(job, "budget_handle", None) is not None else core.budget.snapshot() if core.budget is not None else None,
        "verification": verification,
        "run_status": execution_status,
        # v1.1.0: block accounting for auto-continue (1 when off) —
        # iterations/compactions above are already summed across
        # blocks by _run_with_auto_continue.
        "blocks": blocks,
        "compactions": result.compactions,
        "stub": llm.mock,
        "tokens_in": result.usage.get("prompt_tokens", 0),
        "tokens_out": result.usage.get("completion_tokens", 0),
    }
    if internal_skill is not None:
        job.result = internal_skill.receipt(job.result, tool_calls=tool_calls_log)
    # Save the output before waiting for validation. Cancellation/restart must
    # preserve the answer while leaving configured-but-unfinished proof unknown.
    current_run = await runs_dao.get_run(job.run_id)
    saved_preliminary = await runs_dao.update_run_status(job.run_id, status=current_run["status"], result=job.result,
        metadata={**(current_run.get("metadata") or {}), "verification": verification})
    if internal_skill is not None and saved_preliminary is None:
        from ..run_errors import RunPersistenceError
        raise RunPersistenceError("Owned preliminary receipt could not be confirmed")
    validation_blocked = None
    if (execution_status == "completed" and params.get("mode", "chat") != "plan"
            and not result.truncated and job.validate_command):
        from ..agent.core import _check_rule
        from ..app import get_sessions_dao
        from ..workspace_ctx import resolve_root_for_session
        from .host_session_gate import require_chat_session

        async def validation_binding_ok() -> bool:
            try:
                await require_chat_session(session_id, internal_agent=(
                    internal_skill if internal_agent is not None else None))
                sessions_dao = get_sessions_dao()
                if sessions_dao is None:
                    return False
                current = await sessions_dao.get(session_id)
                root = await resolve_root_for_session(session_id)
                latest = await sessions_dao.get(session_id)
                return bool(current and latest and current.get("project_id") == job.project_id
                    and latest.get("project_id") == job.project_id
                    and (current.get("engine") or "native") == "native"
                    and (latest.get("engine") or "native") == "native"
                    and str(root) == job.workspace)
            except Exception:
                return False

        def validation_actions() -> set[str | None]:
            return {_check_rule(perm_store, "project_validation"),
                    _check_rule(perm_store, "exec_command")}

        actions = validation_actions()
        approved = False
        if "deny" in actions:
            validation_blocked = "validation_denied"
        elif not await validation_binding_ok():
            validation_blocked = "validation_workspace_changed"
        elif "ask" in actions:
            register_gater(ctx.server, session_id, gater)
            await _set_run_state("awaiting_approval")
            try:
                approved = await gater.request_consent(tool="project_validation", args={
                    "command": job.validate_command, "cwd": job.workspace,
                    "scope": "host shell; unrestricted filesystem and network"}, session_id=session_id)
            finally:
                unregister_gater(ctx.server, session_id)
            await _set_run_state("running")
            if not approved:
                validation_blocked = "validation_denied"
        if validation_blocked is None:
            actions = validation_actions()
            if "deny" in actions or ("ask" in actions and not approved):
                validation_blocked = "validation_denied"
            elif not await validation_binding_ok():
                validation_blocked = "validation_workspace_changed"
            else:
                actions = validation_actions()
                if "deny" in actions or ("ask" in actions and not approved):
                    validation_blocked = "validation_denied"
    if validation_blocked:
        verification = {**verification, "verdict": "unknown", "reason": validation_blocked,
                        "requires_review": True}
    else:
        verification = await verify_native_result(status=execution_status, mode=params.get("mode", "chat"),
            command=job.validate_command, workspace=job.workspace, truncated=bool(result.truncated), base=verification_base)
    job.result["verification"] = verification
    if internal_skill is not None:
        job.result = internal_skill.receipt(job.result, tool_calls=tool_calls_log)
    await recorder.complete(result, blocks=blocks, receipt=job.result, verification=verification)
    await ctx.reply(job.result)


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

    cancelled, updates = await get_run_service(ctx.server).cancel_session(str(session_id))
    for run in updates:
        await _notify_run(ctx, "run.updated", {"run": run})
    if cancelled:
        await ctx.reply({"ok": True, "cancelled": str(session_id)})
        return
    entry = _ACTIVE_RUNS.get(str(session_id))
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
    all_tool_calls = list(getattr(result, "tool_calls", None) or [])
    all_tool_results = list(getattr(result, "tool_results", None) or [])
    while (
        getattr(core.config, "auto_continue", False)
        and result.truncated
        and not result.cancelled
        # M2 — a budget pause must never auto-continue: the caller set
        # a spending cap, and continuing would silently exceed it.
        and not getattr(result, "budget_exhausted", False)
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
        all_tool_calls.extend(getattr(result, "tool_calls", None) or [])
        all_tool_results.extend(getattr(result, "tool_results", None) or [])
    if blocks > 1:
        result = replace(result, iterations=total_iterations, compactions=total_compactions,
                         tool_calls=all_tool_calls, tool_results=all_tool_results)
    return result, blocks


async def handle_agent_continue_run(params: Any, ctx: Context) -> None:
    """``agent.continue_run`` — resume a budget-truncated turn (v1.1.0).

    Block-budget semantics: ``max_iterations`` caps a single *block* of
    work, not the whole task. This handler validates a pinned source (or
    the latest run for legacy clients) ended truncated, then delegates to
    :func:`handle_agent_send_message` with a source-specific idempotency key —
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
    if not isinstance(session_id, str):
        await ctx.reply_error(-32602, "session_id must be a string")
        return
    source_id = params.get("run_id")
    if source_id is not None and (not isinstance(source_id, str) or not source_id.strip()):
        await ctx.reply_error(-32602, "run_id must be a non-empty string")
        return

    # The selected source must have ended truncated. Degraded
    # storage (no db) → nothing recorded → nothing to continue.
    try:
        from ..app import get_db
        from ..storage.dao.runs import AgentRunsDAO
        from .handler_utils import HandlerError
        from .host_session_gate import require_public_recovery

        db = get_db()
        if db is None:
            await ctx.reply({"ok": False, "error": "storage unavailable"})
            return
        await require_public_recovery(session_id, source_id)
        dao = AgentRunsDAO(db)
        if source_id is not None:
            run = await dao.get_run(source_id)
        else:
            runs = await dao.list_runs(session_id=session_id, limit=1)
            run = runs[0] if runs else None
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
        return
    except Exception as exc:
        await ctx.reply({"ok": False, "error": f"run lookup failed: {exc}"})
        return

    metadata = (run or {}).get("metadata") or {}
    if source_id is None and run and run.get("session_id") == session_id:
        try:
            await require_public_recovery(session_id, run["id"])
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
            return
    if (
        not run
        or run.get("session_id") != session_id
        or run.get("status") not in ("completed", "cancelled")
        or not metadata.get("truncated")
    ):
        await ctx.reply({"ok": False, "error": "no truncated run to continue"})
        return

    # One continuation per source block. The database commits its admission,
    # source linkage and continued marker together before any model/tool work.
    # Pin the source in the prompt fingerprint so unrelated idempotent requests
    # can never silently join a continuation of a different block.
    import hashlib

    key = "continue:" + hashlib.sha256(run["id"].encode()).hexdigest()
    skill_spec = None
    skill_intent = {}
    previous = await dao.request_payload(run["id"])
    if "skill_execution" in metadata or (previous and "skill_intent" in previous):
        from ..skill_execution import SkillExecutionSpec
        try:
            skill_spec = SkillExecutionSpec(metadata.get("skill_execution"))
        except ValueError as exc:
            await ctx.reply_error(-32602, str(exc))
            return
        if previous is None or "skill_intent" not in previous:
            await ctx.reply_error(-32602, "Saved skill intent is missing; start a new task")
            return
        skill_intent = {"skill_intent": previous["skill_intent"]}
    agent_spec = None
    if "agent_execution" in metadata or (previous and "agent_intent" in previous):
        from ..agent_execution import AgentExecutionSpec
        try:
            agent_spec = AgentExecutionSpec(metadata.get("agent_execution"))
            if previous is None or "agent_intent" not in previous:
                raise ValueError("Saved agent intent is missing; start a new task")
        except ValueError as exc:
            await ctx.reply_error(-32602, str(exc))
            return
        skill_intent = {"agent_intent": previous["agent_intent"]}
    await handle_agent_send_message(
        {"session_id": session_id, "content": _CONTINUE_PROMPT + f"\n[Continuation of run {run['id']}]",
         "mode": run.get("mode", "chat"), "idempotency_key": key,
         "budget_tokens": (metadata.get("budget") or {}).get("limit_tokens"),
         "restrict_network_tools": metadata.get("restrict_network_tools", False), **skill_intent}, ctx,
        resumed_from_id=run["id"], continuation=True,
        required_model=metadata.get("model_snapshot"), required_workspace=metadata.get("workspace_snapshot"),
        required_permission_rules=metadata.get("permission_rules_snapshot"),
        internal_skill=skill_spec, internal_agent=agent_spec,
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
    codex_request = getattr(ctx.server, "_codex_ask_routes", {}).get(request_id)
    if codex_request is not None:
        if params.get("session_id") != codex_request.session_id or params.get("run_id") != codex_request.run_id:
            await ctx.reply({"ok": False, "error": "Request binding does not match"})
            return
        if params.get("cancel") is True:
            resolved = codex_request.resolve_ask_user(request_id, None)
        else:
            resolved = codex_request.resolve_ask_user(request_id, answers)
        await ctx.reply({"ok": resolved, "request_id": request_id,
                         **({} if resolved else {"error": "Invalid answer or expired request"})})
        return
    if not isinstance(answers, list) or not answers:
        await ctx.reply_error(-32602, "answers must be a non-empty array")
        return

    if "session_id" in params or "run_id" in params:
        live = getattr(ctx.server, "_live_ask_users", {}).get(request_id)
        event = live[0] if live else None
        if (not event or params.get("session_id") != event.get("session_id")
                or params.get("run_id") != event.get("run_id")):
            await ctx.reply({"ok": False, "error": "Request binding does not match"})
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
