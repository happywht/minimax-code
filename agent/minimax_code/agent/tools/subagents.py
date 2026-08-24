"""Sub-agent orchestration tools exposed to the main AgentCore."""

from __future__ import annotations

import asyncio
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


def _snapshot(run_id: str, result: dict[str, Any]) -> dict[str, Any]:
    """Project an invoke envelope into the check/wait response shape."""
    return {
        "run_id": run_id,
        "status": "cancelled" if result.get("cancelled") else "completed",
        "partial": bool(result.get("partial", False)),
        "text": result.get("text", ""),
        "iterations": result.get("iterations", 0),
        "tool_calls": result.get("tool_calls", []),
        "stub": bool(result.get("stub", True)),
        "usage": result.get("usage") or {},
        "truncated": bool(result.get("truncated", False)),
    }


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


async def _drive_run(
    *,
    runtime: Any,
    handle: Any,
    run_id: str,
    sub_session: str,
    prompt: str,
    row: dict[str, Any],
    parent: str | None,
    emit: Any,
    run_dao: Any,
    run_metadata: dict[str, Any],
) -> dict[str, Any]:
    """Drive one sub-agent run through its full lifecycle.

    Shared by the foreground (``wait=True``) and background
    (``wait=False``) spawn paths — persistence, progress events, the
    wall-clock partial guard, and the ``_ACTIVE_RUNS`` cancel channel
    behave identically either way. Returns the invoke envelope (with a
    ``partial`` flag when the wall clock fired).
    """
    from ...orchestrator.subagent import subagent_wall_clock_s

    agent_id = row.get("id")
    emit_event = lambda status, progress, summary, **kw: _subagent_event(  # noqa: E731
        run_id=run_id,
        agent_id=agent_id,
        parent_session_id=parent,
        status=status,
        progress=progress,
        summary=summary,
        **kw,
    )

    # Register for IPC cancellation (agent.cancel_subagent) and
    # process-wide shutdown sweeps. Imported lazily: builtins pulls
    # in the whole agent stack, so a module-level import would loop.
    try:
        from ...ipc.builtins import _ACTIVE_RUNS

        _ACTIVE_RUNS[run_id] = {"core": handle.core, "type": "subagent"}
    except Exception:  # pragma: no cover — defensive
        logger.debug("subagent run not registered for cancellation", exc_info=True)

    # Live progress bridge: mirror the core's tool callbacks as
    # tool_call / tool_result progress events (fail-open). The previous
    # callbacks are restored afterwards so a reused handle is untouched.
    async def _on_tool_call(call: dict[str, Any]) -> None:
        await _emit_safe(
            emit, emit_event("tool_call", 0.55, str(call.get("name") or "tool")[:80])
        )

    async def _on_tool_result(call: dict[str, Any], _result: Any) -> None:
        await _emit_safe(
            emit,
            emit_event("tool_result", 0.70, f"{call.get('name') or 'tool'} done"[:80]),
        )

    prev_on_tool_call = getattr(handle.core, "on_tool_call", None)
    prev_on_tool_result = getattr(handle.core, "on_tool_result", None)
    handle.core.on_tool_call = _on_tool_call
    handle.core.on_tool_result = _on_tool_result

    try:
        await _emit_safe(emit, emit_event("started", 0.05, prompt[:80]))

        # Wall-clock guard: shield the invoke so a timeout cancels the
        # core *cooperatively* (run() checkpoints return a partial
        # AgentRunResult) instead of destroying the coroutine. The
        # wind-down is bounded: an in-flight tool waits out its own
        # tool_timeout and an LLM stall its stall timeout.
        inner = asyncio.ensure_future(
            runtime.invoke(handle, session_id=sub_session, request=prompt)
        )
        wall = subagent_wall_clock_s()
        partial = False
        try:
            if wall and wall > 0:
                result = await asyncio.wait_for(asyncio.shield(inner), timeout=wall)
            else:
                result = await inner
        except TimeoutError:
            partial = True
            if handle.core is not None:
                handle.core.cancel()
            result = await inner  # cooperative wind-down — keeps partials
    except Exception as exc:
        if run_dao is not None:
            try:
                await run_dao.update_run_status(
                    run_id, status="failed", error=str(exc), metadata=run_metadata
                )
            except Exception:  # pragma: no cover — defensive
                logger.debug("subagent run failure not persisted", exc_info=True)
        await _emit_safe(
            emit, emit_event("failed", 1.0, str(exc)[:80], error=str(exc))
        )
        raise
    finally:
        try:
            from ...ipc.builtins import _ACTIVE_RUNS

            _ACTIVE_RUNS.pop(run_id, None)
        except Exception:  # pragma: no cover — defensive
            pass
        handle.core.on_tool_call = prev_on_tool_call
        handle.core.on_tool_result = prev_on_tool_result

    cancelled = bool(result.get("cancelled", False)) or partial
    result = {**result, "cancelled": cancelled, "partial": partial}
    summary_text = str(result.get("text", ""))
    completion_metadata = {
        **run_metadata,
        "iterations": result.get("iterations", 0),
        "stub": bool(result.get("stub", True)),
        "partial": partial,
        # check_subagent / wait_subagent read the outcome from the run
        # row once the task is gone (agent restart, late poll).
        "result_text": summary_text,
    }
    if run_dao is not None:
        try:
            await run_dao.update_run_status(
                run_id,
                status="cancelled" if cancelled else "completed",
                metadata=completion_metadata,
            )
        except Exception:  # pragma: no cover — defensive
            logger.debug("subagent run completion not persisted", exc_info=True)

    await _emit_safe(
        emit,
        emit_event(
            "cancelled" if cancelled else "completed",
            1.0,
            summary_text[:80] or ("partial result" if partial else "done"),
            text=summary_text or None,
        ),
    )
    return result


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
        "not distract the main conversation. Set wait=false to run in the background and "
        "collect later with check_subagent / wait_subagent."
    )
    parameters = {
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "description": (
                    "Sub-agent name from list_subagents, for example 'general' "
                    "or 'security-reviewer'."
                ),
            },
            "prompt": {
                "type": "string",
                "description": "Focused task prompt for the sub-agent.",
            },
            "parent_session_id": {
                "type": "string",
                "description": "Optional current chat session id for traceability.",
            },
            "wait": {
                "type": "boolean",
                "description": (
                    "Wait for the sub-agent to finish (default true). Set false to "
                    "run in background; poll with check_subagent(run_id) or collect "
                    "with wait_subagent(run_id)."
                ),
                "default": True,
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
        wait: bool = True,
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

        drive_kwargs = dict(
            runtime=runtime,
            handle=handle,
            run_id=run_id,
            sub_session=sub_session,
            prompt=prompt,
            row=row,
            parent=parent,
            emit=emit,
            run_dao=run_dao,
            run_metadata=run_metadata,
        )

        if not wait:
            # Background path: hand the full lifecycle to a task the
            # module-level registry keeps alive (weak task refs would
            # let the GC kill it mid-flight) and return immediately.
            from ...orchestrator.subagent import register_background_run

            task = asyncio.get_running_loop().create_task(
                _drive_run(**drive_kwargs), name=f"subagent:{run_id}"
            )
            register_background_run(run_id, task)
            return ToolResult.ok(
                {
                    "agent": row["name"],
                    "run_id": run_id,
                    "session_id": sub_session,
                    "parent_session_id": parent,
                    "status": "running",
                    "wait": False,
                    "hint": (
                        "running in background — poll check_subagent(run_id) "
                        "or collect with wait_subagent(run_id)"
                    ),
                }
            )

        result = await _drive_run(**drive_kwargs)
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
                "partial": bool(result.get("partial", False)),
                "truncated": bool(result.get("truncated", False)),
            }
        )


async def _lookup_finished_run(run_id: str) -> ToolResult:
    """Read a finished (or restarted-away) run's outcome from storage."""
    try:
        from ...app import get_db
        from ...storage.dao.runs import AgentRunsDAO

        db = get_db()
    except Exception:  # pragma: no cover — defensive
        db = None
    if db is None:
        return ToolResult.fail(f"unknown run_id: {run_id!r}")
    row = await AgentRunsDAO(db).get_run(run_id)
    if row is None:
        return ToolResult.fail(f"unknown run_id: {run_id!r}")
    meta = row.get("metadata") or {}
    return ToolResult.ok(
        {
            "run_id": run_id,
            "status": row.get("status"),
            "agent": meta.get("agent_name"),
            "partial": bool(meta.get("partial", False)),
            "text": meta.get("result_text", ""),
            "iterations": meta.get("iterations", 0),
            "error": row.get("error"),
        }
    )


@register_tool
class CheckSubagentTool(Tool):
    name = "check_subagent"
    description = (
        "Check the current status of a sub-agent run started with "
        "spawn_subagent(wait=false). Returns 'running' while in flight, or the "
        "final outcome (text / iterations / partial flag) once finished."
    )
    parameters = {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "Run id returned by spawn_subagent.",
            },
        },
        "required": ["run_id"],
        "additionalProperties": False,
    }

    async def run(self, run_id: str) -> ToolResult:
        if not run_id.strip():
            return ToolResult.fail("run_id is required")
        from ...orchestrator.subagent import get_background_run

        task = get_background_run(run_id.strip())
        if task is None:
            # Not in flight — the persisted row carries the outcome.
            return await _lookup_finished_run(run_id.strip())
        if task.done() and not task.cancelled():
            exc = task.exception()
            if exc is not None:
                return ToolResult.ok(
                    {"run_id": run_id, "status": "failed", "error": str(exc)}
                )
            return ToolResult.ok(_snapshot(run_id, task.result()))
        return ToolResult.ok({"run_id": run_id, "status": "running"})


@register_tool
class WaitSubagentTool(Tool):
    name = "wait_subagent"
    description = (
        "Wait for a background sub-agent run to finish and return its full result. "
        "On timeout the run keeps going — the response says 'running' and you can "
        "call wait_subagent again."
    )
    parameters = {
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "Run id returned by spawn_subagent(wait=false).",
            },
            "timeout_s": {
                "type": "number",
                "description": "How long to wait before returning 'running'.",
                "default": 120,
            },
        },
        "required": ["run_id"],
        "additionalProperties": False,
    }

    async def run(self, run_id: str, timeout_s: float = 120) -> ToolResult:
        if not run_id.strip():
            return ToolResult.fail("run_id is required")
        from ...orchestrator.subagent import get_background_run

        rid = run_id.strip()
        task = get_background_run(rid)
        if task is None:
            return await _lookup_finished_run(rid)
        try:
            # Shield: a timeout cancels the wrapper, never the run —
            # the task keeps its lifecycle (persistence + events) intact.
            result = await asyncio.wait_for(
                asyncio.shield(task), timeout=max(0.1, float(timeout_s))
            )
        except TimeoutError:
            return ToolResult.ok(
                {
                    "run_id": rid,
                    "status": "running",
                    "timeout": True,
                    "hint": "still running; call wait_subagent again or check_subagent",
                }
            )
        return ToolResult.ok(_snapshot(rid, result))
