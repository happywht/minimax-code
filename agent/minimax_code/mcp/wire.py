"""ACP wire protocol constants for MCP-over-ACP (R34).

Ports ``xai-grok-mcp/src/wire.rs`` — the single source of truth for the
``x.ai/mcp/*`` ACP wire strings. These method / ``_meta`` keys are part of the
cross-language MCP-over-ACP protocol; referencing these constants instead of
re-typing the literals keeps the agent and any SDK peer from drifting apart.

Product fusion: MiniMax Code's IPC is JSON-RPC 2.0 (its own method namespace
like ``agent.*`` / ``session.*``). When MiniMax Code speaks MCP — invoking
external tool servers, or exposing its own tools as an in-process MCP server —
these ``x.ai/mcp/*`` keys are the cross-boundary method names. Centralizing
them here means the agent, a future SDK peer, and any MCP wiring all share one
definition site (no magic strings scattered across handlers).
"""

from __future__ import annotations

__all__ = [
    "MCP_CALL",
    "MCP_SDK_CALL",
    "MCP_SERVERS",
    "MCP_SDK",
]

#: Forward tool-invocation method (client -> agent): ``x.ai/mcp/call``.
#:
#: The client asks the agent to invoke an MCP tool on a server the agent is
#: connected to, outside the LLM loop.
MCP_CALL: str = "x.ai/mcp/call"

#: Reverse zero-IPC tool-invocation method (agent -> client): ``x.ai/mcp/sdk_call``.
#:
#: The agent invokes a tool that lives in the SDK's in-process MCP server by
#: sending the MCP JSON-RPC message back to the client over the ACP reverse
#: channel. Distinct from :data:`MCP_CALL` so the two disjoint schemas don't
#: share a method string for metrics/tracing.
MCP_SDK_CALL: str = "x.ai/mcp/sdk_call"

#: ``session/new`` ``_meta`` key listing in-process SDK MCP servers: ``x.ai/mcp/servers``.
MCP_SERVERS: str = "x.ai/mcp/servers"

#: ``initialize`` ``_meta`` capability flag advertising in-process SDK MCP support
#: (enables ``transport="acp"``): ``x.ai/mcp/sdk``.
MCP_SDK: str = "x.ai/mcp/sdk"
