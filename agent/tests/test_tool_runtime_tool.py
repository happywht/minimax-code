"""Tests for the R109 tool_runtime tool module.

Covers:

- ``ContentBlock``: ``Text`` / ``Image`` / ``Resource`` constructor +
  ``to_dict`` / ``from_dict`` round trip, ``Image`` ``mimeType`` alias on
  decode, optional-field omission (``metadata`` empty, ``media_id`` /
  ``filename`` / ``path`` ``None``), unknown-type raise on both directions
- ``ToolProgress``: ``Text`` / ``Content`` / ``Custom`` constructor +
  round trip, ``Content`` blocks nesting, ``Custom`` payload transparency,
  unknown-kind raise
- ``ToolStreamItem``: ``Progress`` / ``Terminal`` constructors,
  ``is_terminal`` / ``is_error`` discrimination (Ok vs Err)
- ``terminal_only``: yields exactly one ``Terminal`` item (no error)
- ``with_progress``: yields ``Progress`` then the awaited ``Terminal``
  (both Ok and Err paths)
- ``TypedToolOutput``: ``from_value`` derives ``model_output`` via the
  lazy-imported ``extract_content_blocks`` (fake render injected),
  ``with_chat_completion_output`` builder returns self, direct construction
- ``ToolVariant``: ``Default`` / ``Variant`` constructors + ``is_default``
- ``default_capabilities``: returns the all-off instance
- the three trait objects (``Tool`` / ``ToolDyn`` / ``ToolFamily``) and
  the two aliases (``ArcTool`` / ``ArcToolFamily``) are importable
"""

from __future__ import annotations

import sys
import types
from collections.abc import AsyncIterator
from contextlib import contextmanager

import pytest

from minimax_code.tool_protocol.ids import ToolId
from minimax_code.tool_runtime.error import ToolError
from minimax_code.tool_runtime.tool import (
    ArcTool,
    ArcToolFamily,
    ContentBlock,
    Tool,
    ToolDyn,
    ToolFamily,
    ToolProgress,
    ToolStream,
    ToolStreamItem,
    ToolVariant,
    TypedToolOutput,
    default_capabilities,
    terminal_only,
    with_progress,
)

# ---------------------------------------------------------------------------
# Fake render injector — TypedToolOutput.from_value lazily imports
# extract_content_blocks from render at call time; render lands in R110, so
# tests inject a stub module here.
# ---------------------------------------------------------------------------

_RENDER_MOD = "minimax_code.tool_runtime.render"


@contextmanager
def fake_render(extract_fn):
    """Inject a stub ``render`` module exporting ``extract_content_blocks``.

    Restores (or removes) the prior ``sys.modules`` entry on exit so the
    injection never leaks across tests.
    """
    mod = types.ModuleType(_RENDER_MOD)
    mod.extract_content_blocks = extract_fn
    saved = sys.modules.get(_RENDER_MOD)
    sys.modules[_RENDER_MOD] = mod
    try:
        yield
    finally:
        if saved is None:
            sys.modules.pop(_RENDER_MOD, None)
        else:
            sys.modules[_RENDER_MOD] = saved


# ---------------------------------------------------------------------------
# ContentBlock
# ---------------------------------------------------------------------------


def test_content_block_text_round_trip():
    b = ContentBlock.Text("hello")
    d = b.to_dict()
    assert d == {"type": "text", "text": "hello"}
    back = ContentBlock.from_dict(d)
    assert back.type == "text"
    assert back.text == "hello"


def test_content_block_image_round_trip_with_optional_fields():
    b = ContentBlock.Image(
        mime_type="image/png",
        data="base64==",
        media_id="m1",
        filename="f.png",
        metadata={"alt": "x"},
    )
    d = b.to_dict()
    assert d["type"] == "image"
    assert d["mime_type"] == "image/png"
    assert d["data"] == "base64=="
    assert d["media_id"] == "m1"
    assert d["filename"] == "f.png"
    assert d["metadata"] == {"alt": "x"}
    # path is None -> omitted.
    assert "path" not in d
    back = ContentBlock.from_dict(d)
    assert back.mime_type == "image/png"
    assert back.data == "base64=="
    assert back.media_id == "m1"
    assert back.filename == "f.png"
    assert back.metadata == {"alt": "x"}
    assert back.path is None


def test_content_block_image_omits_empty_metadata_and_nones():
    b = ContentBlock.Image(mime_type="image/png", data="x")
    d = b.to_dict()
    assert "metadata" not in d
    assert "media_id" not in d
    assert "filename" not in d
    assert "path" not in d
    back = ContentBlock.from_dict(d)
    assert back.metadata == {}


def test_content_block_image_accepts_camel_case_mime_type_alias():
    # Rust #[serde(alias = "mimeType")]: decode reads mime_type then mimeType.
    back = ContentBlock.from_dict(
        {"type": "image", "mimeType": "image/jpeg", "data": "y"}
    )
    assert back.mime_type == "image/jpeg"
    assert back.data == "y"


def test_content_block_resource_round_trip():
    b = ContentBlock.Resource(uri="file:///x", mime_type="text/plain", text="hi")
    d = b.to_dict()
    assert d == {
        "type": "resource",
        "uri": "file:///x",
        "mime_type": "text/plain",
        "text": "hi",
    }
    back = ContentBlock.from_dict(d)
    assert back.uri == "file:///x"
    assert back.mime_type == "text/plain"
    assert back.text == "hi"


def test_content_block_resource_omits_optional_fields():
    b = ContentBlock.Resource(uri="file:///x")
    d = b.to_dict()
    assert d == {"type": "resource", "uri": "file:///x"}
    assert "mime_type" not in d
    assert "text" not in d


def test_content_block_resource_accepts_camel_case_mime_type_alias():
    back = ContentBlock.from_dict(
        {"type": "resource", "uri": "u", "mimeType": "application/json"}
    )
    assert back.mime_type == "application/json"


def test_content_block_from_dict_unknown_type_raises():
    with pytest.raises(ValueError):
        ContentBlock.from_dict({"type": "unknown"})


def test_content_block_to_dict_unknown_type_raises():
    b = ContentBlock(type="bogus")
    with pytest.raises(ValueError):
        b.to_dict()


# ---------------------------------------------------------------------------
# ToolProgress
# ---------------------------------------------------------------------------


def test_tool_progress_text_round_trip():
    p = ToolProgress.Text("running")
    d = p.to_dict()
    assert d == {"kind": "text", "text": "running"}
    back = ToolProgress.from_dict(d)
    assert back.kind == "text"
    assert back.text == "running"


def test_tool_progress_content_round_trip_nests_blocks():
    p = ToolProgress.Content([ContentBlock.Text("a"), ContentBlock.Text("b")])
    d = p.to_dict()
    assert d["kind"] == "content"
    assert len(d["blocks"]) == 2
    assert d["blocks"][0] == {"type": "text", "text": "a"}
    back = ToolProgress.from_dict(d)
    assert back.kind == "content"
    assert len(back.blocks) == 2
    assert back.blocks[0].text == "a"
    assert back.blocks[1].text == "b"


def test_tool_progress_content_empty_blocks_round_trips():
    # Empty blocks list serialises to an empty array (not omitted).
    p = ToolProgress.Content([])
    d = p.to_dict()
    assert d == {"kind": "content", "blocks": []}
    back = ToolProgress.from_dict(d)
    assert back.blocks == []


def test_tool_progress_custom_round_trip_payload_transparent():
    payload = {"lines": ["x", "y"], "exit_code": 0}
    p = ToolProgress.Custom("bash_output_chunk", payload)
    d = p.to_dict()
    assert d == {
        "kind": "custom",
        "subkind": "bash_output_chunk",
        "payload": payload,
    }
    back = ToolProgress.from_dict(d)
    assert back.subkind == "bash_output_chunk"
    assert back.payload == payload


def test_tool_progress_from_dict_unknown_kind_raises():
    with pytest.raises(ValueError):
        ToolProgress.from_dict({"kind": "bogus"})


def test_tool_progress_to_dict_unknown_kind_raises():
    p = ToolProgress(kind="bogus")
    with pytest.raises(ValueError):
        p.to_dict()


# ---------------------------------------------------------------------------
# ToolStreamItem
# ---------------------------------------------------------------------------


def test_tool_stream_item_progress_constructor():
    item = ToolStreamItem.Progress(ToolProgress.Text("hi"))
    assert item.kind == "progress"
    assert item.progress.text == "hi"
    assert item.terminal is None
    assert not item.is_terminal()
    assert not item.is_error()


def test_tool_stream_item_terminal_ok():
    item = ToolStreamItem.Terminal("result")
    assert item.kind == "terminal"
    assert item.terminal == "result"
    assert item.is_terminal()
    assert not item.is_error()


def test_tool_stream_item_terminal_error():
    err = ToolError.execution(ToolId("bash"), "boom")
    item = ToolStreamItem.Terminal(err)
    assert item.is_terminal()
    assert item.is_error()
    assert item.terminal is err


def test_tool_stream_item_progress_is_not_error():
    # A Progress item is never an error regardless of payload.
    item = ToolStreamItem.Progress(ToolProgress.Text("x"))
    assert not item.is_error()


# ---------------------------------------------------------------------------
# terminal_only / with_progress — async generator behaviour
# ---------------------------------------------------------------------------


async def test_terminal_only_yields_single_terminal_item():
    out: list[ToolStreamItem[str]] = []
    async for item in terminal_only("result"):  # type: ignore[arg-type]
        out.append(item)
    assert len(out) == 1
    assert out[0].is_terminal()
    assert out[0].terminal == "result"
    assert not out[0].is_error()


async def test_terminal_only_carries_error():
    err = ToolError.timeout(ToolId("bash"), "slow")
    out: list[ToolStreamItem[str]] = []
    async for item in terminal_only(err):  # type: ignore[arg-type]
        out.append(item)
    assert len(out) == 1
    assert out[0].is_terminal()
    assert out[0].is_error()


async def test_with_progress_yields_progress_then_terminal():
    async def terminal_future() -> str:
        return "done"

    progress = ToolProgress.Text("working...")
    out: list[ToolStreamItem[str]] = []
    async for item in with_progress(progress, terminal_future()):
        out.append(item)
    assert len(out) == 2
    # First item is the Progress, second is the awaited Terminal.
    assert not out[0].is_terminal()
    assert out[0].progress.text == "working..."
    assert out[1].is_terminal()
    assert out[1].terminal == "done"
    assert not out[1].is_error()


async def test_with_progress_terminal_can_carry_error():
    err = ToolError.execution(ToolId("bash"), "boom")

    async def terminal_future() -> ToolError:
        return err

    out: list[ToolStreamItem[str]] = []
    async for item in with_progress(ToolProgress.Text("x"), terminal_future()):
        out.append(item)
    assert len(out) == 2
    assert out[0].progress.text == "x"
    assert out[1].is_terminal()
    assert out[1].is_error()


async def test_with_progress_awaits_terminal_between_items():
    # The Terminal item must come AFTER the awaitable resolves: assert
    # ordering by observing a side effect set inside the awaitable.
    order: list[str] = []

    async def terminal_future() -> str:
        order.append("awaited")
        return "done"

    async for item in with_progress(ToolProgress.Text("p"), terminal_future()):
        if not item.is_terminal():
            order.append("progress")
        else:
            order.append("terminal")
    # progress yielded first, then the awaitable runs, then terminal.
    assert order == ["progress", "awaited", "terminal"]


# ---------------------------------------------------------------------------
# TypedToolOutput
# ---------------------------------------------------------------------------


def test_typed_tool_output_direct_construction_defaults():
    tto = TypedToolOutput(tool_id=ToolId("bash"), value={"x": 1})
    assert str(tto.tool_id) == "bash"
    assert tto.value == {"x": 1}
    assert tto.model_output == []
    assert tto.chat_completion_output is None


def test_typed_tool_output_from_value_extracts_content_blocks():
    captured: dict[str, object] = {}

    def fake_extract(value):
        captured["value"] = value
        return [ContentBlock.Text("derived")]

    with fake_render(fake_extract):
        tto = TypedToolOutput.from_value(ToolId("fs:read"), {"path": "/a"})
    assert str(tto.tool_id) == "fs:read"
    assert tto.value == {"path": "/a"}
    assert tto.model_output == [ContentBlock.Text("derived")]
    # extract_content_blocks received the raw value.
    assert captured["value"] == {"path": "/a"}


def test_typed_tool_output_from_value_passes_through_empty_extraction():
    def fake_extract(value):
        return []

    with fake_render(fake_extract):
        tto = TypedToolOutput.from_value(ToolId("bash"), {"ok": True})
    # Empty model_output signals "use auto-extraction" downstream.
    assert tto.model_output == []


def test_typed_tool_output_with_chat_completion_output_returns_self():
    tto = TypedToolOutput(tool_id=ToolId("bash"), value=None)
    sentinel = object()
    returned = tto.with_chat_completion_output(sentinel)  # type: ignore[arg-type]
    assert returned is tto
    assert tto.chat_completion_output is sentinel


def test_typed_tool_output_with_chat_completion_output_clears_with_none():
    tto = TypedToolOutput(
        tool_id=ToolId("bash"), value=None, chat_completion_output=object()
    )
    tto.with_chat_completion_output(None)
    assert tto.chat_completion_output is None


# ---------------------------------------------------------------------------
# ToolVariant
# ---------------------------------------------------------------------------


def test_tool_variant_default():
    v = ToolVariant.Default()
    assert v.kind == "default"
    assert v.name is None
    assert v.is_default()


def test_tool_variant_named():
    v = ToolVariant.Variant("preview")
    assert v.kind == "variant"
    assert v.name == "preview"
    assert not v.is_default()


def test_tool_variant_default_equals_default():
    # Rust derive(PartialEq): two Default variants are equal.
    assert ToolVariant.Default() == ToolVariant.Default()
    assert ToolVariant.Variant("x") == ToolVariant.Variant("x")
    assert ToolVariant.Default() != ToolVariant.Variant("x")
    assert ToolVariant.Variant("x") != ToolVariant.Variant("y")


# ---------------------------------------------------------------------------
# default_capabilities
# ---------------------------------------------------------------------------


def test_default_capabilities_returns_all_off_instance():
    caps = default_capabilities()
    # Rust ToolCapabilities::default() — every flag off.
    assert caps.is_read_only is False
    assert caps.supports_cancel is False
    # max_concurrency / streaming / timeout etc. default to None.
    assert caps.streaming is None
    assert caps.max_concurrency is None


def test_default_capabilities_returns_fresh_instance_each_call():
    a = default_capabilities()
    b = default_capabilities()
    assert a is not b  # not a shared singleton


# ---------------------------------------------------------------------------
# Protocol symbols importable (trait objects + aliases)
# ---------------------------------------------------------------------------


def test_trait_protocols_and_aliases_are_importable():
    # The three trait objects land as Protocol classes; the two aliases
    # alias them. Smoke test that the symbols resolve.
    assert isinstance(Tool, type)
    assert isinstance(ToolDyn, type)
    assert isinstance(ToolFamily, type)
    assert ArcTool is ToolDyn  # ArcTool = ToolDyn (Arc<dyn ToolDyn> -> ref-shared)
    assert ArcToolFamily is ToolFamily


def test_tool_stream_alias_importable():
    # ToolStream is a type alias for AsyncIterator[ToolStreamItem[T]] — a
    # subscripted GenericAlias whose origin is collections.abc.AsyncIterator.
    from typing import get_origin

    assert ToolStream is not None
    assert get_origin(ToolStream) is AsyncIterator


# ---------------------------------------------------------------------------
# ToolStreamItem is generic over T
# ---------------------------------------------------------------------------


def test_tool_stream_item_is_generic():
    # Generic[T] dataclass: subscripting should not raise.
    _ = ToolStreamItem[str]  # noqa: F841 — just assert subscript works
