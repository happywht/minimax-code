"""Contract tests for ``mcp_adapter.bridge`` (R181 + R182 + R183).

R181 ports grok-build's ``xai-computer-hub-mcp-adapter/src/bridge.rs`` type
layer's first leaf -- the :class:`McpBridgeConfig` value object. R182 ports
the module-private ``translate_mcp_result`` free function (the MCP
``tools/call`` result -> :class:`ToolOutputWire` mapper).

R181 -- ``McpBridgeConfig`` invariants
--------------------------------------

These tests pin four invariants the Rust
``#[derive(Debug, Clone)] pub struct McpBridgeConfig`` guarantees:

1. **Frozen value object** -- a ``@dataclass(frozen=True)`` so it is immutable
   and hashable (the Python ``Clone`` equivalent for config shared by reference
   into the bridge actor).
2. **Field shape** -- exactly ``session_id`` (a
   :class:`~minimax_code.tool_protocol.ids.SessionId`) and ``namespace`` (an
   ``Option<String>`` -> ``str | None``), in Rust declaration order.
3. **Value semantics** -- equal field values are equal; distinct
   ``SessionId`` values are distinct; the object is hashable.
4. **Debug repr** -- the dataclass auto ``__repr__`` carries the class name and
   the field values (the Rust ``Debug`` derive).

R182 -- ``translate_mcp_result`` invariants
-------------------------------------------

These tests pin the four branches of ``bridge.rs``'s ``translate_mcp_result``,
in Rust evaluation order:

1. **Empty content** -> :class:`Text` with an empty string, regardless of
   ``is_error``.
2. **Error** -> :class:`Text` joining text blocks with ``"\\n"``; non-text
   blocks are dropped (a warning is logged when *all* blocks are non-text).
3. **Single text block** -> :class:`Text` carrying that block's text.
4. **Multi-block or any non-text block** -> :class:`Mcp` with content blocks
   mapped 1:1 to wire :data:`McpBlock` variants.

R183 -- ``McpToolHandler`` struct + Debug + accessors
-----------------------------------------------------

These tests pin the synchronous surface of ``bridge.rs``'s
``McpToolHandler`` (R183 lands the struct + ``Debug`` + ``tool_id`` /
``description`` / ``input_schema``; ``handle_call`` is deferred to R184):

1. **Debug repr** -- only ``tool_id`` surfaces; the other three fields are
   omitted (Rust ``finish_non_exhaustive``).
2. **tool_id accessor** -- returns the exact ``ToolId`` passed at
   construction.
3. **description accessor** -- ``ToolDescription::new`` +
   ``with_namespace`` when a namespace is configured; a missing definition
   description defaults to an empty string.
4. **input_schema accessor** -- passes the definition's schema through by
   identity (``None`` when the definition carries none).

R184 -- ``McpToolHandler.handle_call`` (async forward + metrics orchestration)
-----------------------------------------------------------------------------

These tests pin the async surface of ``bridge.rs``'s ``McpToolHandler``
``async fn handle_call`` -- the hub tool-call -> MCP ``tools/call`` forward
path with duration/error metric orchestration:

1. **Ok arm** -- :func:`translate_mcp_result` + ``to_wire`` +
   :meth:`TypedToolOutput.from_value` round-trip; the terminal item carries a
   :class:`TypedToolOutput` whose ``value`` is the wire dict (single text /
   empty content / multi-block).
2. **Err arm** -- a :class:`McpTransportError` raised by ``call_tool``
   becomes a plain :meth:`ToolError.execution` (``kind == EXECUTION``,
   ``details == {"tool_id": ...}``, **no** ``.source``).
3. **Serde failure** -- a serialisation error (``translate_mcp_result`` or
   ``from_value`` raising) becomes :meth:`ToolError.execution` **with** the
   causal exception attached via ``.with_source``.
4. **Forwarded args** -- ``call_tool`` receives the definition's tool name
   and the caller's ``args`` verbatim.
5. **Metrics orchestration** -- ``mcp_call_duration_observe`` is called on
   every path (Rust observes the duration before the result ``match``);
   ``mcp_error`` is called once on the Err arm and once on the serde-failure
   arm (never on a clean Ok arm).

R185 -- ``McpBridge`` actor + ``McpBridgeHandle`` (connect orchestration)
-----------------------------------------------------------------------

These tests pin ``bridge.rs``'s ``McpBridge`` actor and its ``connect`` result
envelope ``McpBridgeHandle``:

1. **connect success** -- ``initialize`` + ``list_tools`` + ``filter_map``;
   the returned handle carries the server info and a handler per survivor tool.
2. **initialize failure** -- ``mcp_error`` bumped once, the causal
   :class:`McpError` re-raised, ``close`` NOT called (nothing to close).
3. **list_tools failure** -- best-effort ``close`` attempted once, ``mcp_error``
   bumped once, the causal :class:`McpError` re-raised.
4. **list_tools failure + close failure** -- the close failure is logged but
   does not mask the original list_tools error.
5. **invalid-name filter** -- a tool whose name fails ``ToolId`` validation is
   skipped (``filter_map`` None arm); valid survivors are still bridged.
6. **bridged gauge** -- ``mcp_tools_bridged_set`` records the survivor count
   after discovery; ``shutdown``/``__del__`` reset it to 0.
7. **namespace flow** -- ``config.namespace`` flows into each handler's
   ``description`` accessor.
8. **accessors** -- ``handlers`` / ``server_info`` / ``tool_count``.
9. **Debug reprs** -- ``McpBridge`` and ``McpBridgeHandle`` surface only the
   server name and tool count (``finish_non_exhaustive``).
"""

from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError, fields, is_dataclass
from typing import Any

import pytest

from minimax_code.mcp_adapter import (
    McpBridgeConfig,
    McpCallResult,
    McpError,
    McpImageContent,
    McpResourceContent,
    McpServerInfo,
    McpTextContent,
    McpToolDefinition,
    McpTransportError,
    metrics,
)
from minimax_code.mcp_adapter.bridge import (
    McpBridge,
    McpBridgeHandle,
    McpToolHandler,
    translate_mcp_result,
)
from minimax_code.mcp_adapter.transport import McpTransport
from minimax_code.tool_protocol.ids import SessionId, ToolId
from minimax_code.tool_protocol.output_wire import (
    ImageBlock,
    Mcp,
    ResourceBlock,
    Text,
    TextBlock,
)
from minimax_code.tool_runtime.context import ToolCallContext
from minimax_code.tool_runtime.error import ToolError, ToolErrorKind
from minimax_code.tool_runtime.tool import TypedToolOutput
from minimax_code.tool_types import ToolDescription

# ---------------------------------------------------------------------------
# Frozen value object (R181)
# ---------------------------------------------------------------------------


def test_config_is_a_frozen_dataclass() -> None:
    """``#[derive(Debug, Clone)]`` -> ``@dataclass(frozen=True)``."""
    assert is_dataclass(McpBridgeConfig)
    assert McpBridgeConfig.__dataclass_params__.frozen is True


def test_config_field_assignment_raises_frozen_instance_error() -> None:
    """Frozen -> accidental reassignment is rejected at runtime."""
    cfg = McpBridgeConfig(session_id=SessionId("s-1"), namespace=None)
    with pytest.raises(FrozenInstanceError):
        cfg.namespace = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Field shape (R181)
# ---------------------------------------------------------------------------


def test_config_has_exactly_two_fields_in_rust_declaration_order() -> None:
    """``session_id`` then ``namespace`` -- Rust field order preserved."""
    names = [f.name for f in fields(McpBridgeConfig)]
    assert names == ["session_id", "namespace"]


def test_config_namespace_accepts_str_and_none() -> None:
    """``Option<String>`` -> ``str | None``; both Some and None accepted.

    There is no ``#[serde(default)]`` (the struct is constructed in code, not
    deserialised), so ``namespace`` is required -- a caller passes ``None``
    explicitly when no namespace is wanted.
    """
    sid = SessionId("s-1")
    assert McpBridgeConfig(session_id=sid, namespace="my-server").namespace == "my-server"
    assert McpBridgeConfig(session_id=sid, namespace=None).namespace is None


def test_config_requires_both_fields_no_defaults() -> None:
    """Neither field has a default -> omitting either is a TypeError."""
    with pytest.raises(TypeError):
        McpBridgeConfig(namespace=None)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        McpBridgeConfig(session_id=SessionId("s-1"))  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Value semantics + Debug repr (R181)
# ---------------------------------------------------------------------------


def test_config_equality_and_hash_are_value_based() -> None:
    """``Clone`` + ``PartialEq`` + ``Hash`` -> same fields are equal + hashable."""
    a = McpBridgeConfig(session_id=SessionId("s-1"), namespace="ns")
    b = McpBridgeConfig(session_id=SessionId("s-1"), namespace="ns")
    assert a == b
    assert hash(a) == hash(b)
    # Distinct SessionId value -> distinct config (SessionId is opaque/valued).
    assert a != McpBridgeConfig(session_id=SessionId("s-2"), namespace="ns")
    # Distinct namespace -> distinct config.
    assert a != McpBridgeConfig(session_id=SessionId("s-1"), namespace=None)
    # Hashable -> usable as a dict key (frozen dataclass contract).
    mapping = {a: "ok"}
    assert mapping[b] == "ok"


def test_config_repr_carries_class_name_and_field_values() -> None:
    """``Debug`` -> dataclass auto ``__repr__`` includes class + fields."""
    cfg = McpBridgeConfig(session_id=SessionId("s-1"), namespace=None)
    text = repr(cfg)
    assert "McpBridgeConfig" in text
    assert "s-1" in text


# ---------------------------------------------------------------------------
# translate_mcp_result -- empty content branch (R182)
# ---------------------------------------------------------------------------


def test_translate_empty_content_returns_empty_text() -> None:
    """Empty ``content`` -> ``Text("")`` (side-effect-only MCP tool)."""
    out = translate_mcp_result(McpCallResult(content=[]))
    assert isinstance(out, Text)
    assert out.text == ""


def test_translate_empty_content_ignores_is_error_flag() -> None:
    """Empty content short-circuits before the ``is_error`` check.

    Mirrors Rust: ``content.is_empty()`` is the first branch, so an error
    result with no content blocks still becomes ``Text("")``.
    """
    out = translate_mcp_result(McpCallResult(content=[], is_error=True))
    assert isinstance(out, Text)
    assert out.text == ""


# ---------------------------------------------------------------------------
# translate_mcp_result -- is_error branch (R182)
# ---------------------------------------------------------------------------


def test_translate_error_joins_text_blocks_with_newline() -> None:
    """Error with multiple text blocks -> ``"\\n"``-joined flat text."""
    result = McpCallResult(
        content=[McpTextContent(text="line1"), McpTextContent(text="line2")],
        is_error=True,
    )
    out = translate_mcp_result(result)
    assert isinstance(out, Text)
    assert out.text == "line1\nline2"


def test_translate_error_drops_non_text_blocks() -> None:
    """Error path keeps only text blocks; images/resources are discarded."""
    result = McpCallResult(
        content=[
            McpTextContent(text="err"),
            McpImageContent(mime_type="image/png", data="abc"),
        ],
        is_error=True,
    )
    out = translate_mcp_result(result)
    assert isinstance(out, Text)
    assert out.text == "err"


def test_translate_error_all_non_text_logs_warning_and_returns_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Error with only non-text blocks -> ``Text("")`` + a ``warn!`` parity log.

    Mirrors the Rust ``warn!(content_count = ..., "...")`` site: when an error
    response carries no text blocks at all, the dropped content is logged as a
    warning so it is not silently lost.
    """
    result = McpCallResult(
        content=[McpImageContent(mime_type="image/png", data="abc")],
        is_error=True,
    )
    with caplog.at_level(logging.WARNING, logger="minimax_code.mcp_adapter.bridge"):
        out = translate_mcp_result(result)
    assert isinstance(out, Text)
    assert out.text == ""
    assert any("non-text blocks" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# translate_mcp_result -- single text block branch (R182)
# ---------------------------------------------------------------------------


def test_translate_single_text_block_returns_flat_text() -> None:
    """One text block (non-error) -> ``Text`` carrying that block's text.

    The common case: a tool returning a single text chunk becomes a flat text
    output rather than a one-block ``Mcp`` wrapper.
    """
    out = translate_mcp_result(McpCallResult(content=[McpTextContent(text="hello")]))
    assert isinstance(out, Text)
    assert out.text == "hello"


# ---------------------------------------------------------------------------
# translate_mcp_result -- multi-block / non-text branch (R182)
# ---------------------------------------------------------------------------


def test_translate_multiple_text_blocks_returns_mcp() -> None:
    """Two text blocks -> ``Mcp`` with two ``TextBlock`` (not joined)."""
    result = McpCallResult(
        content=[McpTextContent(text="t1"), McpTextContent(text="t2")],
    )
    out = translate_mcp_result(result)
    assert isinstance(out, Mcp)
    assert len(out.blocks) == 2
    assert all(isinstance(b, TextBlock) for b in out.blocks)
    assert [b.text for b in out.blocks] == ["t1", "t2"]


def test_translate_single_non_text_block_returns_mcp() -> None:
    """A single non-text block -> ``Mcp`` (the single-text fast path is text-only).

    The Rust ``len == 1 && first.is_text()`` guard means a lone image still
    routes to the ``Mcp`` branch with its one mapped block.
    """
    result = McpCallResult(
        content=[McpImageContent(mime_type="image/png", data="abc")],
    )
    out = translate_mcp_result(result)
    assert isinstance(out, Mcp)
    assert len(out.blocks) == 1
    block = out.blocks[0]
    assert isinstance(block, ImageBlock)
    assert block.mime_type == "image/png"
    assert block.data == "abc"


def test_translate_mixed_blocks_map_each_variant_one_to_one() -> None:
    """All three McpContent variants map 1:1 to their wire McpBlock variants."""
    result = McpCallResult(
        content=[
            McpTextContent(text="desc"),
            McpImageContent(mime_type="image/png", data="img"),
            McpResourceContent(uri="file://x", mime_type="text/plain", text="res"),
        ],
    )
    out = translate_mcp_result(result)
    assert isinstance(out, Mcp)
    assert len(out.blocks) == 3
    b0, b1, b2 = out.blocks
    assert isinstance(b0, TextBlock)
    assert b0.text == "desc"
    assert isinstance(b1, ImageBlock)
    assert b1.mime_type == "image/png"
    assert b1.data == "img"
    assert isinstance(b2, ResourceBlock)
    assert b2.uri == "file://x"
    assert b2.mime_type == "text/plain"
    assert b2.text == "res"


# ---------------------------------------------------------------------------
# McpToolHandler -- struct + Debug (R183)
# ---------------------------------------------------------------------------


class _StubMcpTransport(McpTransport):
    """Controllable concrete :class:`McpTransport` for handler + bridge tests.

    R183's three accessors never touch the transport; R184's ``handle_call``
    exercises ``call_tool``; R185's ``McpBridge.connect`` drives
    ``initialize`` / ``list_tools`` / ``close``. The stub:

    * records every ``(name, arguments)`` pair in :attr:`call_args`;
    * ``call_tool`` returns a preloaded :attr:`call_result` or raises a
      preloaded :attr:`call_error` (``NotImplementedError`` with neither);
    * ``initialize`` returns :attr:`server_info` or raises
      :attr:`initialize_error` (``NotImplementedError`` with neither);
    * ``list_tools`` returns :attr:`tools` or raises :attr:`list_tools_error`
      (``NotImplementedError`` with neither);
    * ``close`` raises :attr:`close_error` if set, otherwise returns ``None``,
      and always increments :attr:`close_calls`.

    The defaults keep the R183/R184 call sites (a bare
    ``_StubMcpTransport()``) unchanged.
    """

    def __init__(
        self,
        *,
        call_result: McpCallResult | None = None,
        call_error: McpError | None = None,
        server_info: McpServerInfo | None = None,
        initialize_error: McpError | None = None,
        tools: list[McpToolDefinition] | None = None,
        list_tools_error: McpError | None = None,
        close_error: McpError | None = None,
    ) -> None:
        self.call_result = call_result
        self.call_error = call_error
        self.server_info = server_info
        self.initialize_error = initialize_error
        self.tools = tools
        self.list_tools_error = list_tools_error
        self.close_error = close_error
        #: Every ``(name, arguments)`` pair ``call_tool`` received, in order.
        self.call_args: list[tuple[str, Any]] = []
        #: Number of times ``close`` was called (R185 connect/shutdown paths).
        self.close_calls: int = 0

    async def initialize(self) -> McpServerInfo:
        if self.initialize_error is not None:
            raise self.initialize_error
        if self.server_info is not None:
            return self.server_info
        raise NotImplementedError

    async def list_tools(self) -> list[McpToolDefinition]:
        if self.list_tools_error is not None:
            raise self.list_tools_error
        if self.tools is not None:
            return self.tools
        raise NotImplementedError

    async def call_tool(self, name: str, arguments: Any) -> McpCallResult:
        self.call_args.append((name, arguments))
        if self.call_error is not None:
            raise self.call_error
        if self.call_result is not None:
            return self.call_result
        raise NotImplementedError

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error
        return None


def _make_handler(
    *,
    name: str = "search",
    description: str | None = "Search the web",
    input_schema: Any = None,
    namespace: str | None = None,
    tool_id: str = "mcp:search",
    transport: McpTransport | None = None,
) -> McpToolHandler:
    """Build a handler backed by :class:`_StubMcpTransport`.

    Keyword-only so each accessor/handle_call test states only the fields it
    cares about. ``transport`` defaults to a bare :class:`_StubMcpTransport`
    (R183 accessor tests never call it); R184 handle_call tests pass one
    configured with a ``call_result`` or ``call_error``.
    """
    return McpToolHandler(
        tool_id=ToolId(tool_id),
        definition=McpToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
        ),
        transport=transport if transport is not None else _StubMcpTransport(),
        namespace=namespace,
    )


def test_handler_repr_shows_only_tool_id_and_omits_other_fields() -> None:
    """Rust ``Debug``: ``field("tool_id") + finish_non_exhaustive``.

    Only ``tool_id`` surfaces in the repr; ``namespace`` / ``description`` /
    the input schema never leak -- the hub does not need them for diagnostics
    and the transport may not be ``Debug``-friendly.
    """
    handler = _make_handler(
        namespace="alpha-ns",
        description="zzz-secret-desc",
        input_schema={"q": {"type": "string"}},
    )
    text = repr(handler)
    assert text.startswith("McpToolHandler(tool_id=")
    assert text.endswith(", ...)")
    assert "mcp:search" in text
    # finish_non_exhaustive: the other three fields are omitted entirely.
    assert "alpha-ns" not in text
    assert "zzz-secret-desc" not in text


# ---------------------------------------------------------------------------
# McpToolHandler -- tool_id accessor (R183)
# ---------------------------------------------------------------------------


def test_handler_tool_id_returns_the_constructed_id() -> None:
    """``fn tool_id(&self) -> ToolId { self.tool_id.clone() }``.

    ToolId is an opaque ``str`` newtype; ``clone`` is identity in Python, so
    the accessor returns the exact object passed at construction.
    """
    tid = ToolId("mcp:fetch")
    handler = McpToolHandler(
        tool_id=tid,
        definition=McpToolDefinition(name="fetch", description=None),
        transport=_StubMcpTransport(),
        namespace=None,
    )
    assert handler.tool_id() is tid


# ---------------------------------------------------------------------------
# McpToolHandler -- description accessor (R183)
# ---------------------------------------------------------------------------


def test_handler_description_without_namespace() -> None:
    """``ToolDescription::new`` + no namespace -> ``namespace`` stays ``None``."""
    handler = _make_handler(namespace=None)
    desc = handler.description()
    assert isinstance(desc, ToolDescription)
    assert desc.name == "search"
    assert desc.description == "Search the web"
    assert desc.namespace is None


def test_handler_description_with_namespace_applies_prefix() -> None:
    """``match namespace { Some(ns) => desc.with_namespace(ns), ... }``."""
    handler = _make_handler(namespace="brave")
    desc = handler.description()
    assert desc.namespace == "brave"


def test_handler_description_defaults_missing_definition_description() -> None:
    """``description.unwrap_or_default()`` -> empty string, not ``None``."""
    handler = _make_handler(description=None)
    desc = handler.description()
    assert desc.description == ""


# ---------------------------------------------------------------------------
# McpToolHandler -- input_schema accessor (R183)
# ---------------------------------------------------------------------------


def test_handler_input_schema_passes_through_definition_schema() -> None:
    """``fn input_schema(&self) -> Option<Value> { ... .clone() }``.

    The schema is returned by identity: the Rust ``.clone()`` is a deep
    ``Arc`` bump that has no Python equivalent at this type boundary, so the
    accessor hands back the exact object the definition holds.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
    }
    handler = _make_handler(input_schema=schema)
    assert handler.input_schema() is schema


def test_handler_input_schema_is_none_when_definition_has_none() -> None:
    """``Option::None`` input schema -> ``None`` (no schema advertised)."""
    handler = _make_handler(input_schema=None)
    assert handler.input_schema() is None


# ---------------------------------------------------------------------------
# McpToolHandler -- handle_call helpers (R184)
# ---------------------------------------------------------------------------


class _CallRecorder:
    """Callable metric spy: records every invocation for count/arg assertions.

    A drop-in replacement for the no-op metric stubs in
    :mod:`minimax_code.mcp_adapter.metrics`: it is callable, so it can be
    ``monkeypatch.setattr``-ed over ``metrics.mcp_call_duration_observe`` /
    ``metrics.mcp_error``; it records each call's args/kwargs so a test can
    assert call count and the shape of the observed value.
    """

    def __init__(self) -> None:
        #: Every call as ``(args, kwargs)``.
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> None:
        self.calls.append((args, kwargs))

    @property
    def call_count(self) -> int:
        return len(self.calls)


async def _drain_terminal(handler: McpToolHandler, args: Any) -> Any:
    """Run ``handle_call`` to completion; return the single Terminal payload.

    Mirrors the dispatcher's consumption contract: ``await handle_call(...)``
    yields the async generator, then ``async for`` drains its one ``Terminal``
    item. Returns ``item.terminal`` -- a :class:`TypedToolOutput` on success or
    a :class:`ToolError` on failure.
    """
    stream = await handler.handle_call(ToolCallContext.default(), args)
    items = [item async for item in stream]
    assert len(items) == 1
    assert items[0].kind == "terminal"
    return items[0].terminal


# ---------------------------------------------------------------------------
# McpToolHandler -- handle_call Ok arm (R184)
# ---------------------------------------------------------------------------


async def test_handle_call_success_single_text_returns_typed_output() -> None:
    """Ok arm: single text block -> TypedToolOutput carrying the wire dict.

    Mirrors the Rust Ok arm: ``translate_mcp_result`` -> ``Text``,
    ``serde_json::to_value`` -> ``{"kind": "text", "value": "hello"}``,
    ``TypedToolOutput::from_value`` wraps it. The terminal item is not an
    error and carries the wire dict verbatim in ``value``.
    """
    transport = _StubMcpTransport(
        call_result=McpCallResult(content=[McpTextContent(text="hello")]),
    )
    handler = _make_handler(transport=transport)
    terminal = await _drain_terminal(handler, {"q": "rust"})
    assert isinstance(terminal, TypedToolOutput)
    assert terminal.tool_id == ToolId("mcp:search")
    assert terminal.value == {"kind": "text", "value": "hello"}


async def test_handle_call_success_empty_content_value_is_empty_text() -> None:
    """Ok arm: empty content -> ``Text("")`` wire dict (side-effect-only tool).

    ``translate_mcp_result`` short-circuits empty content to ``Text("")``
    before the ``is_error`` check, so ``value`` is the empty-text wire dict.
    """
    transport = _StubMcpTransport(call_result=McpCallResult(content=[]))
    handler = _make_handler(transport=transport)
    terminal = await _drain_terminal(handler, {})
    assert isinstance(terminal, TypedToolOutput)
    assert terminal.value == {"kind": "text", "value": ""}


async def test_handle_call_success_multi_block_value_is_mcp_wire() -> None:
    """Ok arm: multi-block -> ``Mcp`` wire dict with the mapped blocks.

    ``translate_mcp_result`` routes multi-block content to ``Mcp``; ``to_wire``
    emits ``{"kind": "mcp", "value": {"blocks": [...]}}`` (the adjacent tag
    wraps the struct, so ``blocks`` sits one level deeper than ``kind``).
    """
    transport = _StubMcpTransport(
        call_result=McpCallResult(
            content=[McpTextContent(text="t1"), McpTextContent(text="t2")],
        ),
    )
    handler = _make_handler(transport=transport)
    terminal = await _drain_terminal(handler, {})
    assert isinstance(terminal, TypedToolOutput)
    assert terminal.value == {
        "kind": "mcp",
        "value": {
            "blocks": [
                {"type": "text", "text": "t1"},
                {"type": "text", "text": "t2"},
            ],
        },
    }


# ---------------------------------------------------------------------------
# McpToolHandler -- handle_call Err arm (R184)
# ---------------------------------------------------------------------------


async def test_handle_call_mcp_error_becomes_execution_error_without_source() -> None:
    """Err arm: McpTransportError -> plain ToolError.execution (no source).

    Mirrors the Rust Err arm: the duration is observed, ``mcp_error`` is
    bumped once, and the error becomes ``ToolError::execution`` with the
    exception's ``Display`` form as the detail. The Err arm does NOT attach a
    source (only the Ok-arm serde failure does).
    """
    transport = _StubMcpTransport(call_error=McpTransportError("boom"))
    handler = _make_handler(transport=transport)
    terminal = await _drain_terminal(handler, {})
    assert isinstance(terminal, ToolError)
    assert terminal.kind is ToolErrorKind.EXECUTION
    assert terminal.detail == "transport error: boom"
    assert terminal.details == {"tool_id": "mcp:search"}
    assert terminal.source is None


# ---------------------------------------------------------------------------
# McpToolHandler -- handle_call serde-failure sub-path (R184)
# ---------------------------------------------------------------------------


async def test_handle_call_serde_failure_attaches_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ok-arm serde failure -> ToolError.execution WITH the cause attached.

    Mirrors the Rust Ok arm's ``serde_json::to_value`` Err path: the duration
    was already observed (Ok arm), ``mcp_error`` is bumped once, and the error
    carries the causal exception in ``.source`` via ``.with_source`` (the Err
    arm does not, the serde-failure sub-path does).
    """
    transport = _StubMcpTransport(
        call_result=McpCallResult(content=[McpTextContent(text="ok")]),
    )
    handler = _make_handler(transport=transport)

    def _boom(_result: McpCallResult) -> Any:
        raise RuntimeError("serde exploded")

    monkeypatch.setattr(
        "minimax_code.mcp_adapter.bridge.translate_mcp_result",
        _boom,
    )
    terminal = await _drain_terminal(handler, {})
    assert isinstance(terminal, ToolError)
    assert terminal.kind is ToolErrorKind.EXECUTION
    assert terminal.details == {"tool_id": "mcp:search"}
    assert isinstance(terminal.source, RuntimeError)
    assert str(terminal.source) == "serde exploded"


# ---------------------------------------------------------------------------
# McpToolHandler -- handle_call forwarded args (R184)
# ---------------------------------------------------------------------------


async def test_handle_call_forwards_name_and_arguments_to_transport() -> None:
    """The definition's name + caller's args reach ``call_tool`` verbatim.

    Mirrors ``self.definition.name.clone()`` + the model's ``args`` flowing
    into ``transport.call_tool``. The stub records every pair; the single call
    must carry the definition name (not the tool_id) and the args dict.
    """
    transport = _StubMcpTransport(
        call_result=McpCallResult(content=[McpTextContent(text="ok")]),
    )
    handler = _make_handler(name="custom_tool", transport=transport)
    await _drain_terminal(handler, {"q": "rust", "limit": 10})
    assert transport.call_args == [
        ("custom_tool", {"q": "rust", "limit": 10}),
    ]


# ---------------------------------------------------------------------------
# McpToolHandler -- handle_call metrics orchestration (R184)
# ---------------------------------------------------------------------------


async def test_handle_call_observes_duration_on_success_and_skips_error_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ok arm: duration observed once; ``mcp_error`` NOT called.

    Rust observes the duration before the result ``match`` (so both arms see
    exactly one observation); a clean Ok arm with no serde failure never bumps
    ``mcp_error``.
    """
    duration = _CallRecorder()
    error = _CallRecorder()
    monkeypatch.setattr(metrics, "mcp_call_duration_observe", duration)
    monkeypatch.setattr(metrics, "mcp_error", error)
    transport = _StubMcpTransport(
        call_result=McpCallResult(content=[McpTextContent(text="ok")]),
    )
    handler = _make_handler(transport=transport)
    await _drain_terminal(handler, {})
    assert duration.call_count == 1
    secs = duration.calls[0][0][0]
    assert isinstance(secs, float)
    assert secs >= 0.0
    assert error.call_count == 0


async def test_handle_call_observes_duration_and_error_on_mcp_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Err arm: duration observed once AND ``mcp_error`` bumped once.

    The duration is observed before the match (so the Err arm sees it too);
    the Err arm then bumps ``mcp_error`` exactly once for the transport
    failure.
    """
    duration = _CallRecorder()
    error = _CallRecorder()
    monkeypatch.setattr(metrics, "mcp_call_duration_observe", duration)
    monkeypatch.setattr(metrics, "mcp_error", error)
    transport = _StubMcpTransport(call_error=McpTransportError("boom"))
    handler = _make_handler(transport=transport)
    await _drain_terminal(handler, {})
    assert duration.call_count == 1
    assert error.call_count == 1


async def test_handle_call_observes_error_metric_on_serde_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ok-arm serde failure: duration observed once AND ``mcp_error`` bumped once.

    The duration was observed on the Ok arm (before the serialisation try); the
    serde-failure sub-path bumps ``mcp_error`` exactly once.
    """
    duration = _CallRecorder()
    error = _CallRecorder()
    monkeypatch.setattr(metrics, "mcp_call_duration_observe", duration)
    monkeypatch.setattr(metrics, "mcp_error", error)

    def _boom(_result: McpCallResult) -> Any:
        raise RuntimeError("serde exploded")

    monkeypatch.setattr(
        "minimax_code.mcp_adapter.bridge.translate_mcp_result",
        _boom,
    )
    transport = _StubMcpTransport(
        call_result=McpCallResult(content=[McpTextContent(text="ok")]),
    )
    handler = _make_handler(transport=transport)
    await _drain_terminal(handler, {})
    assert duration.call_count == 1
    assert error.call_count == 1


# ---------------------------------------------------------------------------
# R185 -- McpBridge actor + McpBridgeHandle (connect orchestration)
# ---------------------------------------------------------------------------


def _make_bridge_config(*, namespace: str | None = None) -> McpBridgeConfig:
    """Build a :class:`McpBridgeConfig` for connect-path tests."""
    return McpBridgeConfig(session_id=SessionId("s-1"), namespace=namespace)


async def test_connect_success_returns_handle_with_handlers_and_server_info() -> None:
    """connect: initialize + list_tools + filter_map -> handle envelope."""
    info = McpServerInfo(name="acme", version="1.0")
    tools = [
        McpToolDefinition(name="mcp:fetch", description="fetch"),
        McpToolDefinition(name="mcp:search", description="search"),
    ]
    transport = _StubMcpTransport(server_info=info, tools=tools)
    handle = await McpBridge.connect(transport, _make_bridge_config())
    assert isinstance(handle, McpBridgeHandle)
    assert handle.server_info is info
    assert handle.bridge.tool_count() == 2
    assert [h.tool_id() for h in handle.bridge.handlers()] == [
        ToolId("mcp:fetch"),
        ToolId("mcp:search"),
    ]


async def test_connect_initialize_failure_bumps_error_and_reraises_without_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """initialize Err: bump ``mcp_error`` once, re-raise, no close (nothing to close)."""
    error = _CallRecorder()
    monkeypatch.setattr(metrics, "mcp_error", error)
    transport = _StubMcpTransport(initialize_error=McpTransportError("init boom"))
    with pytest.raises(McpError):
        await McpBridge.connect(transport, _make_bridge_config())
    assert error.call_count == 1
    assert transport.close_calls == 0


async def test_connect_list_tools_failure_attempts_close_bumps_error_and_reraises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """list_tools Err: best-effort close once, bump ``mcp_error`` once, re-raise."""
    error = _CallRecorder()
    monkeypatch.setattr(metrics, "mcp_error", error)
    transport = _StubMcpTransport(
        server_info=McpServerInfo(name="acme", version="1.0"),
        list_tools_error=McpTransportError("list boom"),
    )
    with pytest.raises(McpError):
        await McpBridge.connect(transport, _make_bridge_config())
    assert error.call_count == 1
    assert transport.close_calls == 1


async def test_connect_list_tools_failure_with_close_failure_does_not_mask_original_error() -> None:
    """list_tools Err + close Err: close logged, the original list_tools error re-raised."""
    transport = _StubMcpTransport(
        server_info=McpServerInfo(name="acme", version="1.0"),
        list_tools_error=McpTransportError("list boom"),
        close_error=McpTransportError("close boom"),
    )
    with pytest.raises(McpError, match="list boom"):
        await McpBridge.connect(transport, _make_bridge_config())
    assert transport.close_calls == 1


async def test_connect_skips_tool_with_invalid_name_and_bridges_valid_ones() -> None:
    """filter_map None arm: invalid-name tool skipped, valid survivors bridged."""
    tools = [
        McpToolDefinition(name="mcp:fetch", description="fetch"),
        McpToolDefinition(name="bad name", description="invalid"),
    ]
    transport = _StubMcpTransport(
        server_info=McpServerInfo(name="acme", version="1.0"),
        tools=tools,
    )
    handle = await McpBridge.connect(transport, _make_bridge_config())
    assert handle.bridge.tool_count() == 1
    assert handle.bridge.handlers()[0].tool_id() == ToolId("mcp:fetch")


async def test_connect_sets_bridged_gauge_to_survivor_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``mcp_tools_bridged_set`` called once with the survivor count.

    The returned handle is kept alive for the assertion: CPython's refcount GC
    would otherwise reclaim the bridge the instant ``connect``'s result is
    dropped, firing ``__del__`` (which resets the gauge to 0) before the
    assertion reads ``call_count``.
    """
    gauge = _CallRecorder()
    monkeypatch.setattr(metrics, "mcp_tools_bridged_set", gauge)
    tools = [
        McpToolDefinition(name="mcp:fetch", description="fetch"),
        McpToolDefinition(name="mcp:search", description="search"),
    ]
    transport = _StubMcpTransport(
        server_info=McpServerInfo(name="acme", version="1.0"),
        tools=tools,
    )
    handle = await McpBridge.connect(transport, _make_bridge_config())
    assert gauge.call_count == 1
    assert gauge.calls[0][0] == (2,)
    _ = handle  # keep the bridge alive past the assertion (defuses __del__).


async def test_connect_applies_config_namespace_to_each_handler() -> None:
    """``config.namespace`` flows into each handler's ``description`` accessor."""
    tools = [McpToolDefinition(name="mcp:fetch", description="fetch")]
    transport = _StubMcpTransport(
        server_info=McpServerInfo(name="acme", version="1.0"),
        tools=tools,
    )
    handle = await McpBridge.connect(transport, _make_bridge_config(namespace="brave"))
    assert handle.bridge.handlers()[0].description().namespace == "brave"


async def test_bridge_accessors_handlers_server_info_tool_count() -> None:
    """``handlers`` / ``server_info`` / ``tool_count`` return the constructed state."""
    info = McpServerInfo(name="acme", version="1.0")
    tools = [McpToolDefinition(name="mcp:fetch", description="fetch")]
    transport = _StubMcpTransport(server_info=info, tools=tools)
    bridge = (await McpBridge.connect(transport, _make_bridge_config())).bridge
    assert bridge.server_info() is info
    assert bridge.tool_count() == 1
    handlers = bridge.handlers()
    assert len(handlers) == 1
    assert isinstance(handlers[0], McpToolHandler)


async def test_shutdown_clears_gauge_and_closes_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """shutdown: gauge reset to 0 before ``transport.close()`` is awaited."""
    gauge = _CallRecorder()
    monkeypatch.setattr(metrics, "mcp_tools_bridged_set", gauge)
    transport = _StubMcpTransport(
        server_info=McpServerInfo(name="acme", version="1.0"),
        tools=[McpToolDefinition(name="mcp:fetch", description="fetch")],
    )
    handle = await McpBridge.connect(transport, _make_bridge_config())
    gauge.calls.clear()
    await handle.bridge.shutdown()
    assert transport.close_calls == 1
    assert gauge.calls == [((0,), {})]


async def test_del_clears_bridged_gauge_without_async_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """__del__ (Drop): gauge reset to 0; no async ``close`` attempted at GC time."""
    gauge = _CallRecorder()
    monkeypatch.setattr(metrics, "mcp_tools_bridged_set", gauge)
    transport = _StubMcpTransport(
        server_info=McpServerInfo(name="acme", version="1.0"),
        tools=[McpToolDefinition(name="mcp:fetch", description="fetch")],
    )
    bridge = (await McpBridge.connect(transport, _make_bridge_config())).bridge
    gauge.calls.clear()
    bridge.__del__()
    assert gauge.calls == [((0,), {})]
    assert transport.close_calls == 0


async def test_bridge_repr_and_handle_repr_show_server_and_tool_count() -> None:
    """Debug reprs: server name + ``tool_count``, ``finish_non_exhaustive`` elides the rest."""
    transport = _StubMcpTransport(
        server_info=McpServerInfo(name="acme", version="1.0"),
        tools=[
            McpToolDefinition(name="mcp:fetch", description="fetch"),
            McpToolDefinition(name="mcp:search", description="search"),
        ],
    )
    handle = await McpBridge.connect(transport, _make_bridge_config())
    bridge_text = repr(handle.bridge)
    assert bridge_text.startswith("McpBridge(server=")
    assert "'acme'" in bridge_text
    assert "tool_count=2" in bridge_text
    assert bridge_text.endswith(", ...)")
    handle_text = repr(handle)
    assert handle_text.startswith("McpBridgeHandle(server_info=")
    assert "'acme'" in handle_text
    assert "tool_count=2" in handle_text
    assert handle_text.endswith(", ...)")

