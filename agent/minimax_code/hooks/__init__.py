"""Lifecycle hooks — fusion of Grok's ``xai-grok-hooks`` crate into
MiniMax Code.

Hooks are file-discovered, JSON-defined subprocess callbacks fired at
four points in the agent lifecycle. They let users and integrators
react to (and gate) agent behaviour without touching agent code:

* :data:`~minimax_code.hooks.types.HookEvent.SESSION_START` — session begin.
* :data:`~minimax_code.hooks.types.HookEvent.PRE_TOOL_USE` — before a tool
  call; the ONLY event that can block or short-circuit.
* :data:`~minimax_code.hooks.types.HookEvent.POST_TOOL_USE` — after a tool
  call, with its result.
* :data:`~minimax_code.hooks.types.HookEvent.SESSION_END` — session end.

Everything is **fail-open**: a hook that times out, crashes, or emits
garbage is logged and skipped. Only an explicit ``{"block": true}``
decision from a pre_tool_use hook actually blocks.

Package layout
--------------

* :mod:`.types`    — enums, config, context, decision models.
* :mod:`.registry` — load + index hook specs by event.
* :mod:`.executor` — run hook subprocesses, fail-open, parse decisions.
"""

from __future__ import annotations

from .executor import HookExecutionResult, HookExecutor
from .registry import HookRegistry
from .types import (
    TOOL_EVENTS,
    HookConfig,
    HookContext,
    HookDecision,
    HookEvent,
    HookMatcher,
)

__all__ = [
    "HOOK_EVENTS",
    "HookConfig",
    "HookContext",
    "HookDecision",
    "HookEvent",
    "HookExecutionResult",
    "HookExecutor",
    "HookMatcher",
    "HookRegistry",
    "TOOL_EVENTS",
]


# Convenience: all event names as strings (for IPC / docs).
HOOK_EVENTS = [e.value for e in HookEvent]
