"""Tests for sampler.content_blocks (R202, ``xai-grok-sampling-types`` ``messages.rs``).

Covers the migrated :class:`ContentBlock` 5-variant tagged union + its 3 direct
dependencies (:class:`CacheControl` leaf + :class:`ImageSource` 2-variant tagged
union + :class:`ToolResultContent` untagged recursive union). Mirrors the grok
wire shapes:

- ``#[serde(tag="type", rename_all="snake_case")]`` dispatch on the ``type`` tag
  (:class:`ContentBlock` + :class:`ImageSource`) -- strict, no catch-all (unknown
  tags raise, contrasting the R201 :class:`StopReason` catch-all which must never
  fail a terminal stream).
- ``#[serde(untagged)]`` shape match (:class:`ToolResultContent`: ``str`` vs
  ``list``).
- ``serde_json::Value`` -> ``dict`` for the tool-use ``input``.
- The recursive ``ToolResultContent::Blocks(Vec<ContentBlock>)`` arm.

``serde_json::Value`` -> ``dict.get`` is a pure mapping; no I/O.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler.content_blocks import (
    Base64ImageSource,
    BlocksToolResultContent,
    CacheControl,
    ContentBlock,
    ImageBlock,
    ImageSource,
    TextBlock,
    TextToolResultContent,
    ThinkingBlock,
    ToolResultBlock,
    ToolResultContent,
    ToolUseBlock,
    UrlImageSource,
)

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_thirteen_symbols() -> None:
    """ContentBlock union base + 5 variants + CacheControl + ImageSource union
    (base + 2 variants) + ToolResultContent union (base + 2 variants) = 13
    re-exported symbols."""
    import minimax_code.sampler.content_blocks as content_blocks

    assert len(content_blocks.__all__) == 13
    assert set(content_blocks.__all__) == {
        "Base64ImageSource",
        "BlocksToolResultContent",
        "CacheControl",
        "ContentBlock",
        "ImageBlock",
        "ImageSource",
        "TextBlock",
        "TextToolResultContent",
        "ThinkingBlock",
        "ToolResultBlock",
        "ToolResultContent",
        "ToolUseBlock",
        "UrlImageSource",
    }


# ---------------------------------------------------------------------------
# CacheControl: prompt-cache annotation leaf.
# ---------------------------------------------------------------------------


def test_cache_control_from_payload_preserves_type() -> None:
    cc = CacheControl.from_payload({"type": "ephemeral"})
    assert cc.type_ == "ephemeral"


def test_cache_control_from_payload_missing_type_defaults_ephemeral() -> None:
    cc = CacheControl.from_payload({})
    assert cc.type_ == "ephemeral"


def test_cache_control_default_is_ephemeral() -> None:
    assert CacheControl().type_ == "ephemeral"


# ---------------------------------------------------------------------------
# ImageSource: 2-variant tagged union.
# ---------------------------------------------------------------------------


def test_image_source_base64_variant() -> None:
    src = ImageSource.from_payload(
        {"type": "base64", "media_type": "image/png", "data": "iVBORw0K"}
    )
    assert isinstance(src, Base64ImageSource)
    assert src.media_type == "image/png"
    assert src.data == "iVBORw0K"
    assert isinstance(src, ImageSource)


def test_image_source_url_variant() -> None:
    src = ImageSource.from_payload({"type": "url", "url": "https://x.test/a.png"})
    assert isinstance(src, UrlImageSource)
    assert src.url == "https://x.test/a.png"
    assert isinstance(src, ImageSource)


def test_image_source_unknown_type_raises() -> None:
    """grok tagged union has no catch-all -> an unknown type fails the parse
    (strict, unlike the R201 StopReason catch-all)."""
    with pytest.raises(ValueError, match="unknown image source type"):
        ImageSource.from_payload({"type": "future_source"})


# ---------------------------------------------------------------------------
# ContentBlock.from_payload: 5-variant tagged-union dispatch.
# ---------------------------------------------------------------------------


def test_content_block_text_variant_without_cache_control() -> None:
    block = ContentBlock.from_payload({"type": "text", "text": "hello"})
    assert isinstance(block, TextBlock)
    assert block.text == "hello"
    assert block.cache_control is None
    assert isinstance(block, ContentBlock)


def test_content_block_text_variant_with_cache_control() -> None:
    block = ContentBlock.from_payload(
        {"type": "text", "text": "cached", "cache_control": {"type": "ephemeral"}}
    )
    assert isinstance(block, TextBlock)
    assert block.cache_control is not None
    assert block.cache_control.type_ == "ephemeral"


def test_content_block_image_variant_with_base64_source() -> None:
    block = ContentBlock.from_payload(
        {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": "abc"},
        }
    )
    assert isinstance(block, ImageBlock)
    assert isinstance(block.source, Base64ImageSource)
    assert block.source.data == "abc"


def test_content_block_image_variant_with_url_source() -> None:
    block = ContentBlock.from_payload(
        {"type": "image", "source": {"type": "url", "url": "https://x.test/i.png"}}
    )
    assert isinstance(block, ImageBlock)
    assert isinstance(block.source, UrlImageSource)


def test_content_block_tool_use_variant_with_input_object() -> None:
    """serde_json::Value input -> arbitrary JSON dict preserved as-is."""
    block = ContentBlock.from_payload(
        {
            "type": "tool_use",
            "id": "tu_1",
            "name": "get_weather",
            "input": {"city": "SF", "units": "c"},
        }
    )
    assert isinstance(block, ToolUseBlock)
    assert block.id == "tu_1"
    assert block.name == "get_weather"
    assert block.input == {"city": "SF", "units": "c"}


def test_content_block_tool_result_variant_with_string_content() -> None:
    block = ContentBlock.from_payload(
        {"type": "tool_result", "tool_use_id": "tu_1", "content": "72F"}
    )
    assert isinstance(block, ToolResultBlock)
    assert block.tool_use_id == "tu_1"
    assert isinstance(block.content, TextToolResultContent)
    assert block.content.text == "72F"


def test_content_block_tool_result_variant_with_blocks_content() -> None:
    """Recursive: a tool_result content list parses into ContentBlock variants."""
    block = ContentBlock.from_payload(
        {
            "type": "tool_result",
            "tool_use_id": "tu_1",
            "content": [
                {"type": "text", "text": "result"},
                {
                    "type": "image",
                    "source": {"type": "url", "url": "https://x.test/o.png"},
                },
            ],
        }
    )
    assert isinstance(block, ToolResultBlock)
    assert isinstance(block.content, BlocksToolResultContent)
    assert len(block.content.blocks) == 2
    assert isinstance(block.content.blocks[0], TextBlock)
    assert isinstance(block.content.blocks[1], ImageBlock)


def test_content_block_tool_result_variant_with_cache_control() -> None:
    block = ContentBlock.from_payload(
        {
            "type": "tool_result",
            "tool_use_id": "tu_1",
            "content": "ok",
            "cache_control": {"type": "ephemeral"},
        }
    )
    assert isinstance(block, ToolResultBlock)
    assert block.cache_control is not None
    assert block.cache_control.type_ == "ephemeral"


def test_content_block_thinking_variant() -> None:
    block = ContentBlock.from_payload(
        {"type": "thinking", "thinking": "Hmm...", "signature": "sig123"}
    )
    assert isinstance(block, ThinkingBlock)
    assert block.thinking == "Hmm..."
    assert block.signature == "sig123"


def test_content_block_unknown_type_raises() -> None:
    """grok tagged union has no catch-all -> an unknown type fails the parse
    (contrast the R201 StopReason catch-all, which must never fail a stream)."""
    with pytest.raises(ValueError, match="unknown content block type"):
        ContentBlock.from_payload({"type": "future_block"})


def test_content_block_image_missing_source_raises() -> None:
    with pytest.raises(ValueError, match="source"):
        ContentBlock.from_payload({"type": "image"})


# ---------------------------------------------------------------------------
# ToolResultContent: untagged shape-match union.
# ---------------------------------------------------------------------------


def test_tool_result_content_string_maps_to_text() -> None:
    result = ToolResultContent.from_payload("plain text")
    assert isinstance(result, TextToolResultContent)
    assert result.text == "plain text"


def test_tool_result_content_none_maps_to_empty_text() -> None:
    """A missing/null tool result content tolerates to empty text rather than
    crash the parse (the platform never fails on a malformed tool result)."""
    result = ToolResultContent.from_payload(None)
    assert isinstance(result, TextToolResultContent)
    assert result.text == ""


def test_tool_result_content_list_maps_to_blocks() -> None:
    result = ToolResultContent.from_payload([{"type": "text", "text": "a"}])
    assert isinstance(result, BlocksToolResultContent)
    assert len(result.blocks) == 1
    assert isinstance(result.blocks[0], TextBlock)


def test_tool_result_content_list_skips_non_dict_items() -> None:
    """Non-dict items (a bare string/number in a blocks array) are skipped, not
    crashed on -- forwards-compat with malformed tool results."""
    result = ToolResultContent.from_payload(
        [{"type": "text", "text": "ok"}, "stray", 42, None]
    )
    assert isinstance(result, BlocksToolResultContent)
    assert len(result.blocks) == 1


def test_tool_result_content_invalid_type_raises() -> None:
    with pytest.raises(ValueError, match="must be string or list"):
        ToolResultContent.from_payload(42)


# ---------------------------------------------------------------------------
# Value semantics: frozen + slots + hashable.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "block",
    [
        TextBlock(text="x"),
        ImageBlock(source=UrlImageSource(url="u")),
        ToolUseBlock(id="1", name="n", input={"k": 1}),
        ToolResultBlock(tool_use_id="1", content=TextToolResultContent(text="r")),
        ThinkingBlock(thinking="t", signature="s"),
    ],
)
def test_content_block_variants_are_frozen(block: ContentBlock) -> None:
    """``@dataclass(frozen=True)`` -> mutating any slot raises (mirrors grok's
    immutable struct). Each variant carries at least one field."""
    field_name = next(iter(type(block).__slots__))
    with pytest.raises(FrozenInstanceError):
        setattr(block, field_name, "rewritten")  # type: ignore[misc]


def test_text_block_is_hashable_and_equal() -> None:
    a = TextBlock(text="x")
    b = TextBlock(text="x")
    assert a == b
    assert hash(a) == hash(b)


def test_tool_use_block_input_defaults_none() -> None:
    """``input`` is ``serde_json::Value`` (required on the wire) but defaults to
    ``None`` for ergonomic construction + tolerant shape."""
    block = ToolUseBlock(id="1", name="n")
    assert block.input is None


def test_tool_use_block_distinct_from_stop_reason_tool_use() -> None:
    """Naming guard: :class:`ToolUseBlock` (ContentBlock variant) is a distinct
    class from the R201 ``StopReason::ToolUse`` variant -- the barrel must carry
    both without collision."""
    from minimax_code.sampler.messages import ToolUse as StopReasonToolUse

    assert ToolUseBlock is not StopReasonToolUse
    assert not issubclass(ToolUseBlock, StopReasonToolUse)
    assert not issubclass(StopReasonToolUse, ToolUseBlock)


def test_content_block_variants_share_base() -> None:
    """All 5 ContentBlock variants are subclasses of the union base."""
    assert isinstance(TextBlock(text="x"), ContentBlock)
    assert isinstance(ImageBlock(source=UrlImageSource(url="u")), ContentBlock)
    assert isinstance(ToolUseBlock(id="1", name="n"), ContentBlock)
    assert isinstance(
        ToolResultBlock(tool_use_id="1", content=TextToolResultContent(text="r")),
        ContentBlock,
    )
    assert isinstance(ThinkingBlock(thinking="t", signature="s"), ContentBlock)
