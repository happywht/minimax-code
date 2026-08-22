"""Pydantic models for the lifecycle hooks subsystem.

Mirrors the broader Claude-Code hooks convention (JSON file, per-event
lists, stdin context, stdout JSON decision) so existing community hook
recipes can be reused. Models are permissive (``extra="allow"``) for
forward-compatibility.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict


class _Base(BaseModel):
    """Shared Pydantic config — matches :mod:`minimax_code.mcp.types`."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class HookEvent(StrEnum):
    """Lifecycle events a hook can subscribe to.

    The v1.1.0 loop-iteration pair (``pre/post_loop_iteration``) fires
    once per agent-loop iteration — before the LLM call and after the
    tool batch — so observers can watch long-running turns without
    per-tool granularity.
    """

    SESSION_START = "session_start"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    SESSION_END = "session_end"
    PRE_LOOP_ITERATION = "pre_loop_iteration"
    POST_LOOP_ITERATION = "post_loop_iteration"


# Events that carry a tool_name and therefore honour a matcher.
TOOL_EVENTS: frozenset[HookEvent] = frozenset(
    {HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE}
)


class HookMatcher(_Base):
    """Selects which tool calls fire a pre/post_tool_use hook.

    ``tool_name`` is an :mod:`fnmatch` glob or a list of globs.
    ``None`` (or omitted) matches every tool.
    """

    tool_name: str | list[str] | None = None


class HookConfig(_Base):
    """One hook recipe, as loaded from JSON."""

    event: HookEvent
    command: list[str]
    matcher: HookMatcher | None = None
    timeout: float = 10.0
    env: dict[str, str] | None = None
    pass_stdin: bool = True


class HookContext(_Base):
    """The payload a hook process receives on stdin (JSON)."""

    event: HookEvent
    session_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: Any = None
    cwd: str | None = None


class HookDecision(_Base):
    """A pre_tool_use hook's verdict, parsed from stdout JSON.

    Only meaningful for ``pre_tool_use``. Other events ignore it.
    """

    block: bool = False
    block_reason: str | None = None
    # Let a hook supply a synthetic outcome and skip the real tool.
    suppress_tool: bool = False


__all__ = [
    "TOOL_EVENTS",
    "HookConfig",
    "HookContext",
    "HookDecision",
    "HookEvent",
    "HookMatcher",
]
