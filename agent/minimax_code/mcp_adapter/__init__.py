"""MCP adapter -- bridge MCP servers into the computer hub tool routing (R179+).

Fusion of grok-build's ``xai-computer-hub-mcp-adapter`` crate. The crate
bridges MCP (Model Context Protocol) servers into the computer hub's tool
routing infrastructure: an :class:`~minimax_code.mcp_adapter.bridge.McpBridge`
connects to an MCP server via an
:class:`~minimax_code.mcp_adapter.transport.McpTransport`, discovers the
server's tools, and produces tool handlers that register with a hub
``ToolServerBuilder``.

Architecture::

    MCP Server  <──McpTransport──>  McpBridge  ──handlers──>  ToolServerBuilder
      (stdio/SSE)                 (discover+forward)           (register with hub)

The ``McpTransport`` trait abstracts the wire protocol so the bridge is
testable with in-memory mocks; concrete transports (stdio, HTTP+SSE) are
provided by downstream consumers.

It sits downstream of four already-landed crates:
:mod:`minimax_code.tool_protocol` (R82-R106),
:mod:`minimax_code.tool_runtime` (R107-R114),
:mod:`minimax_code.tool_types` (R65), and
:mod:`minimax_code.computer_hub_sdk` (R133-R178) -- so its landing wires the
MCP discovery surface onto the now-complete computer-hub stack.

Leaf order
----------

The crate's ``lib.rs`` declares four modules (``bridge`` / ``metrics`` /
``transport`` / ``types``). The dependency order -- contract-before-runtime --
is:

1. ``types`` (R179) -- MCP wire type contracts: :class:`McpServerInfo` /
   :class:`McpToolDefinition` / :class:`McpCallResult` / :class:`McpContent` /
   :class:`McpError`. These mirror the MCP spec's JSON-RPC shapes and are
   intentionally decoupled from any transport, so the bridge stays testable
   with in-memory mocks. Landing them first pins the adapter's wire
   vocabulary before the transport trait or the bridge actor consume it.

2. ``transport`` (R180) -- :class:`McpTransport` async trait: the four
   lifecycle coroutines (``initialize`` / ``list_tools`` / ``call_tool`` /
   ``close``) a bridge drives. An ``abc.ABC`` with ``async abstractmethod``
   definitions; concrete stdio / HTTP+SSE transports live in downstream
   consumers, and the trait boundary keeps the bridge testable with
   in-memory mocks.

3. ``bridge`` (R181+) -- the bridge actor: discovers tools from an
   :class:`McpTransport` and registers them with a hub ``ToolServer``. R181
   lands the configuration value object (:class:`McpBridgeConfig`); the actor
   (``connect`` / ``handlers`` / ``server_info`` / ``tool_count`` /
   ``shutdown`` + ``Drop``), ``McpToolHandler``, ``McpBridgeHandle``, and
   ``translate_mcp_result`` land over R182+ in dependency order (config ->
   handler + translate -> actor -> handle).

   The ``metrics`` stub landed in R184 (crate-private, ``pub(crate)`` -- it
   records call/error/tool-count telemetry for the bridge's internals but
   never enters the barrel); the barrel-reconciliation round (R186) mirrors
   ``lib.rs``'s ``pub use`` surface now that every leaf is in. See
   "Barrel reconciliation (R186)" below.

Modelling note
--------------

These types are MCP *standard wire* shapes (serde ``Serialize`` +
``Deserialize`` with camelCase rename and an internally-tagged content
enum), so they use pydantic v2 ``BaseModel`` + ``Field(alias=...)`` +
``populate_by_name=True`` + a ``Field(discriminator="type")`` union -- the
textbook match for round-tripping MCP JSON-RPC. This follows the
:mod:`minimax_code.config_types` convention rather than the hand-written
``*_from_wire`` + ``@dataclass`` convention :mod:`minimax_code.tool_protocol`
uses for its complex adjacent/untagged tagging; the MCP content enum is
plain internally-tagged, which pydantic discriminates natively.
"""

from minimax_code.mcp_adapter.bridge import (
    McpBridge,
    McpBridgeConfig,
    McpBridgeHandle,
    McpToolHandler,
)
from minimax_code.mcp_adapter.transport import McpTransport
from minimax_code.mcp_adapter.types import (
    McpCallResult,
    McpContent,
    McpDecodeError,
    McpError,
    McpImageContent,
    McpProtocolError,
    McpResourceContent,
    McpServerInfo,
    McpTextContent,
    McpTimeoutError,
    McpToolDefinition,
    McpTransportError,
)

__all__ = [
    # bridge.rs barrel (R181) -- McpBridge* + McpToolHandler (4 lib.rs pub use
    # symbols). R181 lands McpBridgeConfig; R183 lands McpToolHandler; the
    # actor + handle land in R185.
    "McpBridge",
    "McpBridgeConfig",
    "McpBridgeHandle",
    "McpToolHandler",
    # transport.rs barrel (R180) -- the McpTransport async trait.
    "McpTransport",
    # types.rs barrel (R179) -- 5 lib.rs pub use symbols + their variants.
    "McpServerInfo",
    "McpToolDefinition",
    "McpCallResult",
    "McpContent",
    "McpError",
    # McpContent union variants (construct targets for the tagged content enum).
    "McpTextContent",
    "McpImageContent",
    "McpResourceContent",
    # McpError subclasses (raise targets for the four thiserror variants).
    "McpTransportError",
    "McpProtocolError",
    "McpTimeoutError",
    "McpDecodeError",
]

#: Barrel reconciliation (R186)
#: ---------------------------
#:
#: Mirrors grok-build ``xai-computer-hub-mcp-adapter/src/lib.rs``'s three
#: ``pub use`` lines (the crate's public surface) at the package root. The
#: barrel is the union of two deliberate halves:
#:
#: * **10 ``lib.rs`` ``pub use`` symbols** (the core crate contract) --
#:   ``bridge`` (4: McpBridge / McpBridgeConfig / McpBridgeHandle /
#:   McpToolHandler), ``transport`` (1: McpTransport), ``types`` (5:
#:   McpCallResult / McpContent / McpError / McpServerInfo /
#:   McpToolDefinition). Pinned by identity checks in
#:   ``tests/test_mcp_adapter_barrel.py``.
#: * **7 Python ergonomics extras** beyond the Rust ``pub use`` -- 3
#:   ``McpContent`` union variants (McpTextContent / McpImageContent /
#:   McpResourceContent) + 4 ``McpError`` subclasses (McpTransportError /
#:   McpProtocolError / McpTimeoutError / McpDecodeError). Rust exposes these
#:   only via ``pub mod types`` (module-path access); Python re-exports them
#:   flat as construct / raise targets (R179 decision: pydantic union
#:   variants and exception hierarchies are top-level citizens in Python's
#:   flat namespace).
#:
#: ``metrics`` (Rust ``pub(crate) mod metrics``) is the deliberate YAGNI
#: boundary: reachable as the ``mcp_adapter.metrics`` submodule for
#: crate-internal callers (mirroring ``crate::metrics::...``) but absent from
#: ``__all__``, so the public surface matches the Rust ``pub use`` contract.
#:
#: Crate completion ledger -- updated as each leaf lands.
#: Landed: types (R179), transport (R180), bridge McpBridgeConfig (R181),
#: bridge translate_mcp_result (R182), bridge McpToolHandler struct + 3
#: accessors (R183), bridge McpToolHandler.handle_call + metrics stub (R184),
#: bridge McpBridge actor + McpBridgeHandle (R185), barrel-reconciliation
#: (R186) -- crate complete.
