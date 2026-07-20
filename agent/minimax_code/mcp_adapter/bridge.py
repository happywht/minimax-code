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
module-private ``translate_mcp_result`` free function. They land in dependency
order:

1. ``McpBridgeConfig`` (R181, landed) -- configuration value object. Pure
   config; consumes only R82 :class:`~minimax_code.tool_protocol.ids.SessionId`.
2. ``translate_mcp_result`` (R182, this leaf) -- the MCP ``tools/call`` result
   -> :class:`~minimax_code.tool_protocol.output_wire.ToolOutputWire` mapper
   (4 branches: empty -> ``Text("")`` / ``is_error`` -> joined text / single
   text block -> ``Text`` / multi-block or non-text -> ``Mcp{blocks}``). A pure
   module-private free function (Rust ``fn`` with no ``pub``; not in
   ``lib.rs``'s ``pub use``), so it lands in this module's ``__all__`` for
   testability but is *not* re-exported by the crate barrel. Consumes R179
   :class:`~minimax_code.mcp_adapter.types.McpContent` /
   :class:`~minimax_code.mcp_adapter.types.McpCallResult` and R83
   :class:`~minimax_code.tool_protocol.output_wire.ToolOutputWire` /
   :data:`~minimax_code.tool_protocol.output_wire.McpBlock`; no forward
   references -- it is the shared dependency of ``McpToolHandler.handle_call``
   and the bridge ``connect`` path, so it lands before the handler (R183+).
3. ``McpToolHandler`` (R183, landed) -- the hub-facing handler for one MCP
   tool. R183 lands the struct + the custom ``Debug`` + three synchronous
   accessors (``tool_id`` / ``description`` / ``input_schema``);
   ``handle_call`` lands in R184 (it consumes :func:`translate_mcp_result`
   plus the ``TypedToolOutput`` / ``ToolError`` / metrics orchestration).
4. ``McpBridge`` actor (R184+) -- ``connect`` / ``handlers`` / ``server_info``
   / ``tool_count`` / ``shutdown`` + the best-effort ``Drop`` close.
5. ``McpBridgeHandle`` (R185+) -- the ``connect`` result envelope
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
* Rust ``fn translate_mcp_result`` (module-private) -> a module-level function
  of the same name. The Rust source inlines the content-block ``match`` inside
  a ``.map`` closure; extracted as :func:`_translate_mcp_block` here because
  ``isinstance`` dispatch reads poorly inside a list comprehension (the
  three-branch mapping is otherwise 1:1 with the Rust arms).
* Rust ``warn!(content_count = ..., "...")`` (``tracing`` structured field) ->
  :func:`logging.warning` with a ``%-``formatted ``content_count`` placeholder
  (the standard-logging idiom for the single structured field the Rust macro
  emits; ``%-`` formatting lets the logger defer formatting unless the level
  is enabled).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from minimax_code.mcp_adapter.transport import McpTransport
from minimax_code.mcp_adapter.types import (
    McpCallResult,
    McpContent,
    McpImageContent,
    McpResourceContent,
    McpTextContent,
    McpToolDefinition,
)
from minimax_code.tool_protocol.ids import SessionId, ToolId
from minimax_code.tool_protocol.output_wire import (
    ImageBlock,
    Mcp,
    McpBlock,
    ResourceBlock,
    Text,
    TextBlock,
    ToolOutputWire,
)
from minimax_code.tool_types import ToolDescription

__all__ = ["McpBridgeConfig", "McpToolHandler", "translate_mcp_result"]

#: Module logger for the ``warn!`` parity site in :func:`translate_mcp_result`.
logger = logging.getLogger(__name__)


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


def translate_mcp_result(result: McpCallResult) -> ToolOutputWire:
    """Map an MCP ``tools/call`` result to a hub :class:`ToolOutputWire`.

    Mirrors ``translate_mcp_result`` in ``bridge.rs``. Four branches, in Rust
    evaluation order:

    * **Empty content** -> :class:`Text` with an empty string, regardless of
      ``is_error`` (side-effect-only MCP tools return no content).
    * **Error** -> :class:`Text` joining every text block with ``"\\n"``;
      non-text blocks are discarded (a warning is logged when *all* blocks are
      non-text, mirroring the Rust ``warn!``).
    * **Single text block** -> :class:`Text` carrying that block's text (the
      common case -- a tool returning one text chunk becomes a flat text
      output).
    * **Multi-block or any non-text block** -> :class:`Mcp` with the content
      blocks mapped 1:1 to wire :data:`McpBlock` variants.

    Args:
        result: The MCP ``tools/call`` result to translate.

    Returns:
        The wire tool output (every branch returns; never ``None``).
    """
    # Empty content -> flat empty text, regardless of is_error.
    if not result.content:
        return Text(text="")

    # Error -> join text blocks; non-text blocks are dropped.
    if result.is_error:
        text_parts = [c.text for c in result.content if isinstance(c, McpTextContent)]
        error_text = "\n".join(text_parts)
        if not error_text:
            logger.warning(
                "MCP error response contained only non-text blocks; "
                "content dropped (content_count=%d)",
                len(result.content),
            )
        return Text(text=error_text)

    # Single text block -> flat text output (the common case).
    only = result.content[0]
    if len(result.content) == 1 and isinstance(only, McpTextContent):
        return Text(text=only.text)

    # Multi-block / non-text -> structured Mcp output.
    blocks = [_translate_mcp_block(c) for c in result.content]
    return Mcp(blocks=blocks)


def _translate_mcp_block(content: McpContent) -> McpBlock:
    """Map one MCP content block to one wire MCP block (``bridge.rs`` inline).

    The Rust source inlines this ``match`` inside ``translate_mcp_result``'s
    ``.map`` closure; extracted as a helper here because ``isinstance``
    dispatch reads poorly inside a list comprehension. The three-branch mapping
    is otherwise 1:1 with the Rust arms.
    """
    if isinstance(content, McpTextContent):
        return TextBlock(text=content.text)
    if isinstance(content, McpImageContent):
        return ImageBlock(mime_type=content.mime_type, data=content.data)
    if isinstance(content, McpResourceContent):
        return ResourceBlock(
            uri=content.uri,
            mime_type=content.mime_type,
            text=content.text,
        )
    # McpContent is a sealed 3-variant union (pydantic discriminated on
    # ``type``), so this is unreachable in practice -- defensive for type
    # checkers and any future variant addition.
    raise TypeError(f"unknown McpContent variant: {type(content).__name__}")


class McpToolHandler:
    """Hub-facing handler for a single MCP tool (``bridge.rs`` McpToolHandler).

    Translates hub ``tool_call_request`` frames into MCP ``tools/call``
    invocations and maps the result back to
    :class:`~minimax_code.tool_protocol.output_wire.ToolOutputWire`. R183 lands
    the struct + the custom ``Debug`` + three synchronous accessors;
    ``handle_call`` lands in R184 (it consumes :func:`translate_mcp_result`
    plus the :class:`~minimax_code.tool_runtime.tool.TypedToolOutput` /
    ``ToolError`` / metrics orchestration).

    A concrete class, not an :class:`abc.ABC` subclass: in Rust
    ``McpToolHandler`` implements ``xai_computer_hub_sdk::ToolServerHandler``,
    but that trait is YAGNI in the Python port (R178 ledger: the live
    ``ToolServer`` actor binds the xAI ``HubConnection`` socket, which MiniMax
    has no consumer for). The handler exposes the trait's four methods with
    the protocol contract documented, not type-enforced -- mirroring the
    ``server.py`` preamble leaves (R175-R177).

    Mirrors ``bridge.rs``'s ``impl ToolServerHandler for McpToolHandler``:

    * ``fn tool_id`` -> :meth:`tool_id`.
    * ``fn description`` -> :meth:`description` (``ToolDescription::new`` +
      ``with_namespace`` when a namespace is configured).
    * ``fn input_schema`` -> :meth:`input_schema`.
    * ``async fn handle_call`` -> deferred to R184.
    """

    def __init__(
        self,
        tool_id: ToolId,
        definition: McpToolDefinition,
        transport: McpTransport,
        namespace: str | None,
    ) -> None:
        # Rust fields are private (impl-internal access only); mirrored as a
        # single-underscore private convention here.
        self._tool_id = tool_id
        self._definition = definition
        self._transport = transport
        self._namespace = namespace

    def __repr__(self) -> str:
        """Rust ``Debug``: ``field("tool_id") + finish_non_exhaustive``.

        Only ``tool_id`` surfaces; ``transport`` / ``definition`` / ``namespace``
        are omitted -- the hub does not need them for diagnostics and the
        transport may not be ``Debug``-friendly.
        """
        return f"McpToolHandler(tool_id={self._tool_id!r}, ...)"

    def tool_id(self) -> ToolId:
        """Return the handler's tool id (``self.tool_id.clone()``)."""
        return self._tool_id

    def description(self) -> ToolDescription:
        """Build the hub-facing :class:`ToolDescription`.

        Mirrors the Rust ``description``: ``ToolDescription::new`` with the
        definition's name and description (``unwrap_or_default`` -> empty
        string when the definition carries none), then ``with_namespace`` when
        a namespace is configured.
        """
        desc = ToolDescription.new(
            name=self._definition.name,
            description=self._definition.description or "",
        )
        if self._namespace is not None:
            return desc.with_namespace(self._namespace)
        return desc

    def input_schema(self) -> Any:
        """Return the tool's JSON input schema (``Option<Value>`` -> ``Any``).

        Mirrors ``self.definition.input_schema.clone()`` -- ``None`` when the
        definition carries no schema.
        """
        return self._definition.input_schema
