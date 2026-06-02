"""Multi-agent orchestrator — sub-agent spawn, task distribution.

Phase 2. This package hosts the runtime pieces that let a primary
agent dispatch work to *sub-agents* — separately-configured agent
identities (their own system prompt, tool allowlist, model) that
the primary can invoke in the middle of a conversation.

The PoC surface is :class:`~minimax_code.orchestrator.subagent.SubAgentRuntime`,
which builds an :class:`AgentCore` from a config row and stubs the
LLM call. Subsequent phases will wire the runtime into real LLM
streaming, a process-pool, and a scheduler-driven "spawn many
sub-agents" path.
"""

from __future__ import annotations

from .subagent import (
    SubAgentConfig,
    SubAgentConfigError,
    SubAgentHandle,
    SubAgentRuntime,
    get_subagent_runtime,
    make_session_id,
    set_subagent_runtime,
)

__all__ = [
    "SubAgentConfig",
    "SubAgentConfigError",
    "SubAgentHandle",
    "SubAgentRuntime",
    "get_subagent_runtime",
    "make_session_id",
    "set_subagent_runtime",
]
