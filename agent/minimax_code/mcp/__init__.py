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
* :mod:`.types` — Pydantic models for the MCP domain (tools, resources,
  prompts, content blocks, initialize handshake, capabilities).
* :mod:`.transport` (R4) — stdio + streamable-HTTP transports.
* :mod:`.client` (R4) — connect to external MCP servers.
* :mod:`.server` (R4) — expose MiniMax's own tools as an MCP server.
* :mod:`.registry` (R5) — bridge external MCP tools into the existing
  :class:`minimax_code.agent.tools.base.ToolRegistry`.

The v0 types here intentionally mirror the public MCP spec
(``2024-11-05``) so a future upstream SDK can drop in without rewriting
handlers. No Rust toolchain is introduced — everything is pure Python.
"""

from __future__ import annotations

from . import protocol, transport, types
from .client import MCPClient, MCPClientError
from .transport import (
    InProcessTransport,
    MCPTransport,
    MCPTransportError,
    StdioTransport,
    make_in_process_pair,
)

__all__ = [
    "InProcessTransport",
    "MCPClient",
    "MCPClientError",
    "MCPTransport",
    "MCPTransportError",
    "StdioTransport",
    "make_in_process_pair",
    "protocol",
    "transport",
    "types",
]
