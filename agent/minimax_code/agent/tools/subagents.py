"""Sub-agent orchestration tools exposed to the main AgentCore."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolResult, register_tool


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
            make_session_id,
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
        session_id = parent_session_id or make_session_id("subagent_tool")
        result = await runtime.invoke(handle, session_id=session_id, request=prompt)
        return ToolResult.ok(
            {
                "agent": row["name"],
                "session_id": session_id,
                "text": result.get("text", ""),
                "iterations": result.get("iterations", 0),
                "tool_calls": result.get("tool_calls", []),
                "stub": bool(result.get("stub", True)),
            }
        )
