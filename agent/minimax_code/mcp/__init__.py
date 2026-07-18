"""Model Context Protocol (MCP) integration — fusion of Grok Build's
``xai-grok-mcp`` design into MiniMax Code.

MCP is the open protocol that lets an agent talk to **external** tool
servers (and expose itself as one). It is the protocol substrate under
Grok's plugin/computer-use story, and the P0 platform pillar that turns
MiniMax Code from a closed monolith into an extensible platform.

Scope of this package
---------------------

* :mod:`.protocol` — wire constants: protocol version, JSON-RPC method
  names, MCP-defined error codes.
* :mod:`.wire` (R34) — xAI ACP-over-MCP wire strings (``x.ai/mcp/*``): the
  cross-language method / ``_meta`` keys for the agent↔SDK reverse channel,
  distinct from the spec-defined methods in :mod:`.protocol`.
* :mod:`.oauth_config` (R34) — BYO per-server OAuth config types parsed out
  of a server's config block.
* :mod:`.types` — Pydantic models for the MCP domain (tools, resources,
  prompts, content blocks, initialize handshake, capabilities).
* :mod:`.transport` (R4) — stdio + streamable-HTTP transports.
* :mod:`.client` (R4) — connect to external MCP servers.
* :mod:`.server` (R4) — expose MiniMax's own tools as an MCP server.
* :mod:`.liveness` (R35) — per-client transport-closed poller decision layer
  (state-machine predicate + poll interval); the host-runtime watcher task
  is a later wiring round.
* :mod:`.registry` (R5) — bridge external MCP tools into the existing
  :class:`minimax_code.agent.tools.base.ToolRegistry`.

The v0 types here intentionally mirror the public MCP spec
(``2024-11-05``) so a future upstream SDK can drop in without rewriting
handlers. The R34 ``wire`` / ``oauth_config`` modules add the xAI-specific
ACP extension strings and the OAuth config shape that grok's crate carries
on top of the standard protocol. No Rust toolchain is introduced —
everything is pure Python.
"""

from __future__ import annotations

from . import protocol, transport, types
from .client import MCPClient, MCPClientError
from .liveness import (
    DEFAULT_POLL_INTERVAL_MS,
    ClientStateKind,
    LivenessCheck,
    McpClientEventKind,
    classify_liveness,
)
from .oauth_config import McpOAuthConfig, McpOAuthConfigMap
from .registry import MCPRegistry, MCPServerConfig, bridged_name
from .transport import (
    InProcessTransport,
    MCPTransport,
    MCPTransportError,
    StdioTransport,
    make_in_process_pair,
)
from .wire import (
    MCP_CALL,
    MCP_SDK,
    MCP_SDK_CALL,
    MCP_SERVERS,
)

__all__ = [
    "InProcessTransport",
    "MCPClient",
    "MCPClientError",
    "MCPServerConfig",
    "MCPRegistry",
    "MCPTransport",
    "MCPTransportError",
    "StdioTransport",
    "bridged_name",
    "make_in_process_pair",
    "protocol",
    "transport",
    "types",
    # wire (R34) — xAI ACP-over-MCP extension strings
    "MCP_CALL",
    "MCP_SDK_CALL",
    "MCP_SERVERS",
    "MCP_SDK",
    # oauth_config (R34)
    "McpOAuthConfig",
    "McpOAuthConfigMap",
    # liveness (R35) — transport-closed poller decision layer
    "DEFAULT_POLL_INTERVAL_MS",
    "ClientStateKind",
    "LivenessCheck",
    "McpClientEventKind",
    "classify_liveness",
]
