"""MCP bridge -- connect an MCP server's tools into the computer hub (R181+).

Fusion of grok-build's ``xai-computer-hub-mcp-adapter/src/bridge.rs`` (~330
non-test lines). The bridge discovers tools from an
:class:`~minimax_code.mcp_adapter.transport.McpTransport` and registers them
with a hub ``ToolServer`` via one ``McpToolHandler`` per tool. Incoming hub
calls are translated to MCP ``tools/call`` and the response is mapped back to
``ToolOutputWire``.

Leaf order (dependency-ordered, R181+)
--------------------------------------

``bridge.rs`` declares four ``pub`` symbols (``McpBridge`` /
``McpBridgeConfig`` / ``McpBridgeHandle`` / ``McpToolHandler``) plus the
``translate_mcp_result`` free function. They land in dependency order:

1. ``McpBridgeConfig`` (R181, this leaf) -- configuration value object. Pure
   config; consumes only R82 :class:`~minimax_code.tool_protocol.ids.SessionId`.
   No forward references (``McpBridgeHandle`` owns an ``McpBridge``, which owns
   ``McpToolHandler`` s, so the config leaf must land first to unblock them).
2. ``McpToolHandler`` + ``translate_mcp_result`` (R182+) -- the hub-facing
   handler for one MCP tool and the MCP-result -> ``ToolOutputWire`` mapper.
3. ``McpBridge`` actor (R183+) -- ``connect`` / ``handlers`` / ``server_info``
   / ``tool_count`` / ``shutdown`` + the best-effort ``Drop`` close.
4. ``McpBridgeHandle`` (R184+) -- the ``connect`` result envelope
   (``bridge`` + ``server_info``), whose ``Debug`` impl reads
   ``bridge.tool_count()`` so it must land after the actor.

``ToolServerHandler`` trait note
--------------------------------

``McpToolHandler`` implements ``xai_computer_hub_sdk::ToolServerHandler`` in
Rust. That trait is YAGNI in the Python port (R178 ledger: the live
``ToolServer`` actor binds the xAI ``HubConnection`` socket, which MiniMax has
no consumer for). So the handler will land as a concrete class exposing the
trait's four methods (``tool_id`` / ``description`` / ``input_schema`` /
``handle_call``), not as an ``abc.ABC`` subclass -- the protocol contract is
documented, not type-enforced, mirroring the server.py preamble leaves
(R175-R177).

Python-specific adaptations (no behavior change)
------------------------------------------------

* Rust ``#[derive(Debug, Clone)] pub struct McpBridgeConfig`` ->
  :func:`dataclasses.dataclass` with ``frozen=True``. ``frozen=True`` gives
  value semantics (immutable + hashable, copy via reconstruction) -- the
  Python equivalent of Rust's ``Clone`` for a config value object; ``Debug``
  is the dataclass auto ``__repr__``.
* Rust ``pub session_id: SessionId`` -> ``session_id: SessionId``.
* Rust ``pub namespace: Option<String>`` -> ``namespace: str | None``. There
  is no ``#[serde(default)]`` (the struct is constructed in code, not
  deserialised), so both fields are required -- a caller passes ``None``
  explicitly when no namespace is wanted.
"""

from __future__ import annotations

from dataclasses import dataclass

from minimax_code.tool_protocol.ids import SessionId

__all__ = ["McpBridgeConfig"]


@dataclass(frozen=True)
class McpBridgeConfig:
    """Configuration for an :class:`McpBridge` instance (``bridge.rs``).

    Mirrors ``McpBridgeConfig`` in ``bridge.rs``. ``namespace`` is the
    optional namespace prefix applied to each tool's
    :class:`~minimax_code.tool_types.ToolDescription` during bridging (the
    handler appends it when asked for its description).

    Frozen (immutable + hashable) -- a config is a value object; once built it
    is shared by reference into the bridge actor and must not mutate, so
    accidental field reassignment raises :class:`FrozenInstanceError`.
    """

    #: Hub session to bind the bridged tools to.
    session_id: SessionId
    #: Optional namespace prefix for tool descriptions (``None`` -> no prefix).
    namespace: str | None
