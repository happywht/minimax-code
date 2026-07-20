"""Contract tests for ``mcp_adapter.bridge`` (R181 + R182).

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
"""

from __future__ import annotations

import logging
from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest

from minimax_code.mcp_adapter import (
    McpBridgeConfig,
    McpCallResult,
    McpImageContent,
    McpResourceContent,
    McpTextContent,
)
from minimax_code.mcp_adapter.bridge import translate_mcp_result
from minimax_code.tool_protocol.ids import SessionId
from minimax_code.tool_protocol.output_wire import (
    ImageBlock,
    Mcp,
    ResourceBlock,
    Text,
    TextBlock,
)

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
