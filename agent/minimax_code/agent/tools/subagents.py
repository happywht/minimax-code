"""Sub-agent orchestration tools exposed to the main AgentCore."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from .base import Tool, ToolResult, register_tool

logger = logging.getLogger(__name__)


def _subagent_event(
    *,
    run_id: str,
    agent_id: str | None,
    parent_session_id: str | None,
    status: str,
    progress: float,
    summary: str,
    text: str | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build an ``agent.subagent_progress`` payload (wire shape parity).

    Mirrors ``handlers_agents._emit_subagent_progress`` so the frontend
    store applies the same projection to both the IPC-path and the
    tool-path events. ``parent_session_id`` is mandatory for routing —
    ``runsForSession`` filters on it.
    """
    payload: dict[str, Any] = {
        "run_id": run_id,
        "agent_id": agent_id or "",
        "parent_session_id": parent_session_id,
        "context_message_id": None,
        "status": status,
        "progress": max(0.0, min(1.0, float(progress))),
        "summary": summary,
    }
    if text is not None:
        payload["text"] = text
    if error is not None:
        payload["error"] = error
    return payload


async def _emit_safe(emit: Any, event: dict[str, Any]) -> None:
    """Push one event through the routed emit callable; fail-open."""
    if emit is None:
        return
    try:
        import inspect

        result = emit("agent.subagent_progress", event)
        if inspect.isawaitable(result):
            await result
    except Exception:  # pragma: no cover — emit must never break the run
        logger.debug("subagent_progress emit failed", exc_info=True)


def _agent_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "description": row.get("description") or "",
        "enabled": bool(row.get("enabled", True)),
        "category": row.get("category") or "",
        "tags": row.get("tags") or [],
        "tools": row.get("tool_allowlist") or [],
        "model": row.get("model"),
    }


async def _agent_dao() -> Any:
    from ...app import get_db, init_runtime
    from ...storage.dao.agents import AgentDAO

    await init_runtime()
    db = get_db()
    if db is None:
        raise RuntimeError("storage layer is not available; sub-agent registry disabled")
    return AgentDAO(db)


async def _ensure_session_row(session_id: str, title: str) -> None:
    """Create a placeholder session row when ``session_id`` is synthetic.

    ``agent_runs.session_id`` has a NOT-NULL FK to ``sessions``; the
    tool path mints its own sub-session id (so sub-agent messages never
    pollute the parent history), and that id needs a row before a run
    can reference it. Mirrors ``TeamOrchestrator._ensure_session`` /
    ``handlers_agents``'s helper. Fail-open: a storage hiccup must not
    block the spawn (the run row is then skipped too).
    """
    try:
        from ...app import get_sessions_dao, init_runtime

        await init_runtime()
        dao = get_sessions_dao()
        if dao is None:
            return
        if await dao.get(session_id) is not None:
            return
        await dao.create(id=session_id, title=title, system_prompt="", model=None)
    except Exception:  # pragma: no cover — defensive
        logger.debug("ensure_session_row(%s) failed; continuing", session_id)


@register_tool
class ListSubagentsTool(Tool):
    name = "list_subagents"
    description = (
        "List configured sub-agents that can be delegated specialist work. "
        "Use this before spawning a sub-agent when you are unsure which agent is available."
    )
    parameters = {
        "type": "object",
        "properties": {
            "include_disabled": {
                "type": "boolean",
                "description": "When true, include disabled sub-agents in the result.",
                "default": False,
            },
        },
        "required": [],
        "additionalProperties": False,
    }

    async def run(self, include_disabled: bool = False) -> ToolResult:
        dao = await _agent_dao()
        rows = await dao.list_all()
        agents = [
            _agent_summary(row)
            for row in rows
            if include_disabled or bool(row.get("enabled", True))
        ]
        return ToolResult.ok({"agents": agents, "count": len(agents)})


@register_tool
class SpawnSubagentTool(Tool):
    name = "spawn_subagent"
    description = (
        "Delegate a focused piece of work to a named sub-agent and return its final result. "
        "Use this for specialist review, parallel research, or focused analysis that should "
        "not distract the main conversation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "description": "Sub-agent name from list_subagents, for example 'general' or 'security-reviewer'.",
            },
            "prompt": {
                "type": "string",
                "description": "Focused task prompt for the sub-agent.",
            },
            "parent_session_id": {
                "type": "string",
                "description": "Optional current chat session id for traceability.",
            },
        },
        "required": ["agent_name", "prompt"],
        "additionalProperties": False,
    }

    async def run(
        self,
        agent_name: str,
        prompt: str,
        parent_session_id: str | None = None,
    ) -> ToolResult:
        # v1.4.0 — exempt this dispatch from the generic tool_timeout
        # ceiling: a sub-agent legitimately runs a whole agent loop
        # (LLM turns + tools) inside one tool call. The value is set as
        # an *instance* attribute at run time (not at import time) so
        # env knob changes and test monkeypatches take effect.
        from ...orchestrator.subagent import subagent_wall_clock_s

        self.dispatch_timeout = subagent_wall_clock_s()

        if not agent_name.strip():
            return ToolResult.fail("agent_name is required")
        if not prompt.strip():
            return ToolResult.fail("prompt is required")

        dao = await _agent_dao()
        row = await dao.get_by_name_or_id(agent_name.strip())
        if row is None:
            return ToolResult.fail(
                f"unknown sub-agent: {agent_name!r}",
                output={"available": [_agent_summary(r) for r in await dao.list_all()]},
            )
        if not bool(row.get("enabled", True)):
            return ToolResult.fail(f"sub-agent {row.get('name')!r} is disabled")

        from ...orchestrator import (
            SubAgentConfig,
            SubAgentRuntime,
            get_subagent_runtime,
        )

        config = SubAgentConfig(
            name=row["name"],
            system_prompt=row.get("system_prompt") or "",
            tool_allowlist=row.get("tool_allowlist"),
            model=row.get("model"),
            id=row.get("id"),
            description=row.get("description") or "",
            enabled=bool(row.get("enabled", True)),
            icon=row.get("icon") or "",
            color=row.get("color") or "",
            category=row.get("category") or "",
            tags=row.get("tags"),
            team_id=row.get("team_id"),
            skills=row.get("skills"),
            max_iterations=int(row.get("max_iterations") or 50),
            temperature=row.get("temperature"),
        )
        runtime = get_subagent_runtime() or SubAgentRuntime()
        handle = runtime.build(config)

        # ---------------------------------------------------------------
        # v1.4.0 — lifecycle: run row + progress events + cancel channel.
        #
        # The sub-agent runs in its own synthetic session so its messages
        # never pollute the parent chat history; the run row references
        # that session (NOT-NULL FK) and carries the parent id in
        # metadata for the timeline. Everything below is fail-open —
        # a storage or emit failure degrades visibility, never the run.
        # ---------------------------------------------------------------
        from ...orchestrator.subagent import (
            current_parent_session,
            resolve_subagent_emit,
        )
        from ...orchestrator.subagent import (
            make_session_id as _make_session_id,
        )

        run_id = f"run_{uuid.uuid4().hex[:12]}"
        parent = parent_session_id or current_parent_session()
        sub_session = _make_session_id("subagent")
        emit = await resolve_subagent_emit(parent)

        run_metadata: dict[str, Any] = {
            "parent_session_id": parent,
            "agent_name": row["name"],
            "agent_id": row.get("id"),
            "prompt": prompt,
            "source": "tool",
        }
        run_dao: Any = None
        try:
            from ...app import get_db
            from ...storage.dao.runs import AgentRunsDAO

            db = get_db()
            if db is not None:
                run_dao = AgentRunsDAO(db)
                await _ensure_session_row(
                    sub_session, title=f"[subagent] {row['name']}"
                )
                await run_dao.create_run(
                    id=run_id,
                    session_id=sub_session,
                    mode="subagent",
                    status="running",
                    title=f"[subagent] {row['name']}",
                    metadata=run_metadata,
                )
        except Exception:
            run_dao = None
            logger.warning("subagent run row not persisted", exc_info=True)

        # Register for IPC cancellation (agent.cancel_subagent) and
        # process-wide shutdown sweeps. Imported lazily: builtins pulls
        # in the whole agent stack, so a module-level import would loop.
        try:
            from ...ipc.builtins import _ACTIVE_RUNS

            _ACTIVE_RUNS[run_id] = {"core": handle.core, "type": "subagent"}
        except Exception:  # pragma: no cover — defensive
            logger.debug("subagent run not registered for cancellation", exc_info=True)

        try:
            await _emit_safe(
                emit,
                _subagent_event(
                    run_id=run_id,
                    agent_id=row.get("id"),
                    parent_session_id=parent,
                    status="started",
                    progress=0.05,
                    summary=prompt[:80],
                ),
            )
            result = await runtime.invoke(
                handle, session_id=sub_session, request=prompt
            )
        except Exception as exc:
            if run_dao is not None:
                try:
                    await run_dao.update_run_status(
                        run_id, status="failed", error=str(exc), metadata=run_metadata
                    )
                except Exception:  # pragma: no cover — defensive
                    logger.debug("subagent run failure not persisted", exc_info=True)
            await _emit_safe(
                emit,
                _subagent_event(
                    run_id=run_id,
                    agent_id=row.get("id"),
                    parent_session_id=parent,
                    status="failed",
                    progress=1.0,
                    summary=str(exc)[:80],
                    error=str(exc),
                ),
            )
            raise
        finally:
            try:
                from ...ipc.builtins import _ACTIVE_RUNS

                _ACTIVE_RUNS.pop(run_id, None)
            except Exception:  # pragma: no cover — defensive
                pass

        cancelled = bool(result.get("cancelled", False))
        final_status = "cancelled" if cancelled else "completed"
        completion_metadata = {
            **run_metadata,
            "iterations": result.get("iterations", 0),
            "stub": bool(result.get("stub", True)),
        }
        if run_dao is not None:
            try:
                await run_dao.update_run_status(
                    run_id, status=final_status, metadata=completion_metadata
                )
            except Exception:  # pragma: no cover — defensive
                logger.debug("subagent run completion not persisted", exc_info=True)

        summary_text = str(result.get("text", ""))
        await _emit_safe(
            emit,
            _subagent_event(
                run_id=run_id,
                agent_id=row.get("id"),
                parent_session_id=parent,
                status="completed" if not cancelled else "cancelled",
                progress=1.0,
                summary=summary_text[:80] or "done",
                text=summary_text or None,
            ),
        )
        return ToolResult.ok(
            {
                "agent": row["name"],
                "run_id": run_id,
                "session_id": sub_session,
                "parent_session_id": parent,
                "text": result.get("text", ""),
                "iterations": result.get("iterations", 0),
                "tool_calls": result.get("tool_calls", []),
                "stub": bool(result.get("stub", True)),
                # v1.4.0 — bubbled run-level facts (P3-12 usage冒泡).
                "usage": result.get("usage") or {},
                "cancelled": bool(result.get("cancelled", False)),
                "truncated": bool(result.get("truncated", False)),
            }
        )
