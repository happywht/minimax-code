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

   Subsequent leaves (``metrics`` stub) will land over R182+; the
   barrel-reconciliation round that mirrors ``lib.rs``'s ``pub use`` surface
   lands once every leaf is in.

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

from minimax_code.mcp_adapter.bridge import McpBridgeConfig
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
    # symbols). R181 lands McpBridgeConfig; the actor + handle + handler land
    # over R182+ in dependency order.
    "McpBridgeConfig",
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

#: Crate completion ledger -- updated as each leaf lands.
#: Landed: types (R179), transport (R180), bridge McpBridgeConfig (R181).
#: Remaining bridge symbols (McpBridge actor, McpBridgeHandle, McpToolHandler,
#: translate_mcp_result), the metrics stub, and the final
#: barrel-reconciliation round are deferred to R182+.
