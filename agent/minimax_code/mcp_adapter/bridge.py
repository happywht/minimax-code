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
3. ``McpToolHandler`` (R183 + R184, landed) -- the hub-facing handler for
   one MCP tool. R183 lands the struct + the custom ``Debug`` + three
   synchronous accessors (``tool_id`` / ``description`` / ``input_schema``);
   R184 lands ``handle_call`` (it consumes :func:`translate_mcp_result`
   plus the :class:`~minimax_code.tool_runtime.tool.TypedToolOutput` /
   :class:`~minimax_code.tool_runtime.error.ToolError` / metrics
   orchestration).
4. ``McpBridge`` actor (R185, landed) -- ``connect`` / ``handlers`` /
   ``server_info`` / ``tool_count`` / ``shutdown`` + the best-effort ``Drop``
   close.
5. ``McpBridgeHandle`` (R185, landed) -- the ``connect`` result envelope
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
import time
from dataclasses import dataclass
from typing import Any

from minimax_code.mcp_adapter import metrics
from minimax_code.mcp_adapter.transport import McpTransport
from minimax_code.mcp_adapter.types import (
    McpCallResult,
    McpContent,
    McpError,
    McpImageContent,
    McpResourceContent,
    McpServerInfo,
    McpTextContent,
    McpToolDefinition,
)
from minimax_code.tool_protocol.ids import IdError, SessionId, ToolId
from minimax_code.tool_protocol.output_wire import (
    ImageBlock,
    Mcp,
    McpBlock,
    ResourceBlock,
    Text,
    TextBlock,
    ToolOutputWire,
)
from minimax_code.tool_runtime.context import ToolCallContext
from minimax_code.tool_runtime.error import ToolError
from minimax_code.tool_runtime.tool import ToolStream, TypedToolOutput, terminal_only
from minimax_code.tool_types import ToolDescription

__all__ = [
    "McpBridge",
    "McpBridgeConfig",
    "McpBridgeHandle",
    "McpToolHandler",
    "translate_mcp_result",
]

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
    :class:`~minimax_code.tool_protocol.output_wire.ToolOutputWire`. R183
    lands the struct + the custom ``Debug`` + three synchronous accessors;
    R184 lands :meth:`handle_call` (it consumes
    :func:`translate_mcp_result` plus the
    :class:`~minimax_code.tool_runtime.tool.TypedToolOutput` /
    :class:`~minimax_code.tool_runtime.error.ToolError` / metrics
    orchestration).

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
    * ``async fn handle_call`` -> :meth:`handle_call` (R184).
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

    async def handle_call(
        self,
        _ctx: ToolCallContext,
        args: Any,
    ) -> ToolStream[TypedToolOutput]:
        """Translate a hub tool call into an MCP ``tools/call`` (R184).

        Mirrors ``bridge.rs``'s ``async fn handle_call``: time the transport
        call, observe the duration metric (unconditionally -- Rust observes
        the duration before the result ``match``, so both the Ok and Err
        arms see exactly one observation), then map the result onto a
        terminal stream item.

        * **Ok arm** -- :func:`translate_mcp_result` maps the MCP result to a
          :class:`~minimax_code.tool_protocol.output_wire.ToolOutputWire`,
          :meth:`to_wire <minimax_code.tool_protocol.output_wire.Text.to_wire>`
          serialises it (the Python equivalent of ``serde_json::to_value``:
          ``ToolOutputWire`` is a hand-rolled adjacent-tagged ``@dataclass``
          whose ``to_wire`` emits ``{"kind": ..., "value": ...}``, *not* a
          pydantic model, so it has no ``model_dump``), and
          :meth:`TypedToolOutput.from_value` wraps it for the dispatcher. A
          serialisation failure (Rust ``serde_json::to_value`` ``Err``) bumps
          ``mcp_error`` and surfaces a :class:`ToolError` with the causal
          exception attached via
          :meth:`~minimax_code.tool_runtime.error.ToolError.with_source`.
        * **Err arm** -- an :class:`~minimax_code.mcp_adapter.types.McpError`
          raised by
          :meth:`~minimax_code.mcp_adapter.transport.McpTransport.call_tool`
          bumps ``mcp_error`` and becomes a plain
          :meth:`~minimax_code.tool_runtime.error.ToolError.execution` (no
          ``.with_source`` -- the Rust Err arm does not attach one).

        The ``_ctx`` parameter mirrors Rust's unused ``_ctx: ToolCallContext``:
        the handler does not consult per-call context (the tool id is fixed at
        construction), so it is accepted and discarded.

        Args:
            _ctx: Per-call context (unused; mirrors Rust ``_ctx``).
            args: The JSON arguments the model produced for this tool's input
                schema.

        Returns:
            A single-item terminal stream carrying either the typed output
            (success) or a :class:`ToolError` (failure). Implemented as an
            ``async def`` that ``return``s the async generator
            :func:`terminal_only` builds -- ``await handle_call(...)`` yields
            the generator, then ``async for`` drains its one ``Terminal`` item
            (the Python equivalent of Rust's ``async fn -> ToolStream``: the
            async fn body runs to completion on ``await``, returning the
            stream; the stream itself is then async-iterated).
        """
        start = time.monotonic()
        tool_id = self._tool_id
        try:
            call_result = await self._transport.call_tool(
                self._definition.name,
                args,
            )
        except McpError as mcp_err:
            # Err arm: Rust observes the duration before the match, then
            # bumps mcp_error once for the transport failure. The Err arm
            # does NOT attach a source (only the Ok-arm serde failure does).
            metrics.mcp_call_duration_observe(time.monotonic() - start)
            metrics.mcp_error()
            return terminal_only(ToolError.execution(tool_id, str(mcp_err)))
        # Ok arm (reached only when call_tool did not raise -- the except arm
        # returns). Observe the duration (Rust observes before the match),
        # then translate -> to_wire -> from_value. A serialisation failure
        # (Rust serde_json::to_value Err) bumps mcp_error and surfaces a
        # ToolError with the causal exception attached.
        metrics.mcp_call_duration_observe(time.monotonic() - start)
        try:
            output = translate_mcp_result(call_result)
            value = output.to_wire()
            terminal: TypedToolOutput | ToolError = TypedToolOutput.from_value(
                tool_id,
                value,
            )
        except Exception as exc:
            metrics.mcp_error()
            terminal = ToolError.execution(tool_id, str(exc)).with_source(exc)
        return terminal_only(terminal)


class McpBridge:
    """Bridge actor connecting an MCP server's tools to the hub (R185).

    Mirrors ``bridge.rs``'s ``McpBridge`` actor. The actor owns three pieces of
    state: the :class:`~minimax_code.mcp_adapter.transport.McpTransport` it
    drives, the list of :class:`McpToolHandler` it discovered (one per server
    tool), and the :class:`~minimax_code.mcp_adapter.types.McpServerInfo`
    returned by ``initialize``. It is constructed exclusively through
    :meth:`connect` -- which performs the ``initialize`` -> ``list_tools`` ->
    ``filter_map`` orchestration -- and torn down through :meth:`shutdown` (or
    the best-effort :meth:`__del__`).

    A concrete class, not an :class:`abc.ABC`: in Rust ``McpBridge`` is a plain
    struct with an ``impl`` block (no trait), and the Python port mirrors that
    one-to-one. ``Arc<dyn McpTransport>`` -> a direct transport reference
    (Python's GC gives shared ownership; the handler list holds its own
    references for the per-tool forward path); ``Vec<Arc<McpToolHandler>>`` ->
    ``list[McpToolHandler]``.

    ``Result<T, McpError>`` -> raise
    :class:`~minimax_code.mcp_adapter.types.McpError`: the :meth:`connect`
    orchestration re-raises the original transport error after bumping
    ``mcp_error`` (mirroring Rust's ``return Err(e)`` -- the causal exception is
    preserved, not wrapped), and :meth:`shutdown` propagates a ``close``
    failure unchanged.
    """

    def __init__(
        self,
        transport: McpTransport,
        handlers: list[McpToolHandler],
        server_info: McpServerInfo,
    ) -> None:
        # Rust fields are private (impl-internal access only); mirrored as a
        # single-underscore private convention here, matching McpToolHandler.
        self._transport = transport
        self._handlers = handlers
        self._server_info = server_info

    def __repr__(self) -> str:
        """Rust ``Debug``: ``field("server", name) + field("tool_count", len)``.

        Mirrors ``impl Debug for McpBridge``: only the server name and handler
        count surface (``finish_non_exhaustive`` elides the transport and the
        handler vector's contents). The transport may not be ``Debug``-friendly,
        so it is omitted -- same rationale as :meth:`McpToolHandler.__repr__`.
        """
        return (
            f"McpBridge(server={self._server_info.name!r}, "
            f"tool_count={len(self._handlers)}, ...)"
        )

    @classmethod
    async def connect(
        cls,
        transport: McpTransport,
        config: McpBridgeConfig,
    ) -> McpBridgeHandle:
        """Connect to an MCP server and discover its tools (``bridge.rs``).

        Mirrors ``bridge.rs``'s ``pub async fn connect``. Three-phase
        orchestration, each failure path bumping ``mcp_error`` exactly once and
        re-raising the original transport error (the Python equivalent of
        Rust's ``return Err(e)`` -- the causal exception is preserved, not
        wrapped):

        1. ``initialize`` -- ``transport.initialize()``; on
           :class:`McpError`, bump ``mcp_error`` and re-raise (the server is
           not reachable, so there is nothing to close).
        2. ``list_tools`` -- ``transport.list_tools()``; on :class:`McpError`,
           best-effort ``transport.close()`` (a close failure is logged at
           ``warning`` but does not mask the original ``list_tools`` error),
           bump ``mcp_error``, and re-raise.
        3. ``filter_map`` -- for each :class:`McpToolDefinition`, build a
           :class:`ToolId` from its name; an invalid name (Rust
           ``ToolId::new`` ``Err``) is logged at ``warning`` and the tool is
           skipped, mirroring ``filter_map``'s ``None``. The survivors become
           :class:`McpToolHandler` instances sharing the transport.

        After discovery ``mcp_tools_bridged_set`` records the survivor count (a
        Rust gauge), the actor is constructed, and a
        :class:`McpBridgeHandle` envelope (``bridge`` + ``server_info``) is
        returned -- the Rust signature returns the handle, not the bare actor,
        so callers get the server info without a second accessor round-trip.

        Args:
            transport: The connected transport to drive (owned by the actor
                after a successful connect; closed on shutdown).
            config: Bridge configuration (the ``namespace`` is applied to each
                handler's description; ``session_id`` is carried on the config
                for future hub-binding and currently unused by the actor).

        Returns:
            The :class:`McpBridgeHandle` wrapping the constructed actor and the
            server info returned by ``initialize``.

        Raises:
            McpError: Re-raised from ``initialize`` or ``list_tools`` after
                ``mcp_error`` is bumped (and, for the ``list_tools`` path, a
                best-effort ``close`` is attempted first).
        """
        # Phase 1: initialize. On failure there is nothing to close (the
        # transport never reached the tools-discovery stage); bump the error
        # counter and re-raise the causal McpError verbatim.
        try:
            server_info = await transport.initialize()
        except McpError:
            metrics.mcp_error()
            raise

        logger.info(
            "MCP bridge connected to server %s (version %s)",
            server_info.name,
            server_info.version,
        )

        # Phase 2: list_tools. On failure the transport is already initialized,
        # so best-effort close before re-raising. A close failure is logged but
        # does not replace the original list_tools error (mirrors Rust's close
        # -then-return-Err ordering, with the warn! parity site on a close
        # error).
        try:
            tools = await transport.list_tools()
        except McpError:
            try:
                await transport.close()
            except McpError as close_err:
                logger.warning(
                    "MCP transport close failed after list_tools error: %s",
                    close_err,
                )
            metrics.mcp_error()
            raise

        logger.debug(
            "MCP server %s advertised %d tools",
            server_info.name,
            len(tools),
        )

        # Phase 3: filter_map definitions -> handlers. An invalid tool name
        # (Rust ToolId::new Err) logs a warning and is skipped (filter_map's
        # None arm); survivors share the transport for the forward call path.
        handlers: list[McpToolHandler] = []
        for definition in tools:
            try:
                tool_id = ToolId(definition.name)
            except IdError as err:
                logger.warning(
                    "Skipping MCP tool with invalid name %r: %s",
                    definition.name,
                    err,
                )
                continue
            handlers.append(
                McpToolHandler(
                    tool_id=tool_id,
                    definition=definition,
                    transport=transport,
                    namespace=config.namespace,
                )
            )

        metrics.mcp_tools_bridged_set(len(handlers))
        bridge = cls(transport, handlers, server_info)
        return McpBridgeHandle(bridge=bridge, server_info=server_info)

    def handlers(self) -> list[McpToolHandler]:
        """Return the discovered tool handlers.

        Mirrors Rust ``&self.handlers`` (a ``&[Arc<McpToolHandler>]`` shared
        reference). Returns the internal list directly -- mirroring Rust's
        shared-reference semantics, the list is logically immutable after
        :meth:`connect`; callers should treat it as read-only.
        """
        return self._handlers

    def server_info(self) -> McpServerInfo:
        """Return the connected server's info (Rust ``&self.server_info``)."""
        return self._server_info

    def tool_count(self) -> int:
        """Return the number of bridged tools (Rust ``self.handlers.len()``)."""
        return len(self._handlers)

    async def shutdown(self) -> None:
        """Shut the bridge down, closing the transport (``bridge.rs``).

        Mirrors ``async fn shutdown``: clear the bridged-tools gauge, then close
        the transport. A :class:`McpError` from ``close`` propagates to the
        caller (Rust returns ``Result<(), McpError>``; Python raises) -- callers
        that want best-effort teardown should catch it.

        Always clears the gauge *before* ``close``: even if ``close`` fails the
        metrics reflect that this bridge no longer serves its tools, matching
        Rust's statement ordering.
        """
        metrics.mcp_tools_bridged_set(0)
        await self._transport.close()

    def __del__(self) -> None:
        """Best-effort cleanup when the actor is garbage-collected (``Drop``).

        Mirrors the synchronous first line of Rust's ``Drop for McpBridge``:
        clear the bridged-tools gauge. The Rust ``Drop`` then spawns a
        best-effort ``transport.close()`` if a tokio runtime is available; in
        Python, ``__del__`` runs at GC time when the event loop may already be
        closed (or absent), so awaiting ``close`` is not reliable. Callers must
        :meth:`shutdown` explicitly for deterministic transport teardown -- this
        is the documented YAGNI boundary (a best-effort async close needs the
        loop to survive; not guaranteed at GC time).
        """
        metrics.mcp_tools_bridged_set(0)


@dataclass(repr=False)
class McpBridgeHandle:
    """The ``connect`` result envelope: ``bridge`` + ``server_info`` (R185).

    Mirrors ``bridge.rs``'s ``pub struct McpBridgeHandle``. The handle exists so
    :meth:`McpBridge.connect` can return both the actor and the server info in
    one value (Rust's signature returns ``McpBridgeHandle``, not the bare
    ``McpBridge``); callers read ``server_info`` without a second accessor
    round-trip and drop the handle to release the actor.

    A :func:`~dataclasses.dataclass` with ``repr=False``: the custom Rust
    ``Debug`` reads ``bridge.tool_count()`` (not the full actor), so the
    dataclass auto ``__repr__`` is suppressed and a manual :meth:`__repr__`
    mirrors the Rust fields. Both fields are plain values (not config), so the
    dataclass is mutable and non-frozen -- unlike
    :class:`McpBridgeConfig`'s ``frozen=True`` value-object semantics.
    """

    bridge: McpBridge
    server_info: McpServerInfo

    def __repr__(self) -> str:
        """Rust ``Debug``: ``field("server_info", name) + field("tool_count", n)``.

        Mirrors ``impl Debug for McpBridgeHandle``: the server name (not the
        full :class:`McpServerInfo`) and ``bridge.tool_count()`` surface, with
        ``finish_non_exhaustive`` eliding the actor itself. Reading
        ``bridge.tool_count()`` is why this type lands after the actor (R185
        leaf ordering: the ``Debug`` impl depends on the actor accessor).
        """
        return (
            f"McpBridgeHandle(server_info={self.server_info.name!r}, "
            f"tool_count={self.bridge.tool_count()}, ...)"
        )
