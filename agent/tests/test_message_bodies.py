"""Tests for sampler.message_bodies (R204, ``xai-grok-sampling-types`` ``messages.rs``).

Covers the 4 migrated "middle-layer" body-shaped leaves: the standalone
:class:`SystemTextBlock` struct (renamed from grok ``TextBlock`` to dodge the R202
:class:`ContentBlock::Text` variant collision), the :class:`SystemParam` +
:class:`MessageContent` untagged string-vs-blocks unions, and the
:class:`StreamDelta` 4-variant tagged union. Mirrors the grok wire shapes:

- ``#[serde(tag="type", rename_all="snake_case")] enum`` (:class:`StreamDelta`)
  -- strict, no catch-all (unknown ``type`` raises, contrasting the R201
  :class:`StopReason` catch-all which must never fail a terminal stream).
- ``#[serde(untagged)] enum`` (:class:`SystemParam` / :class:`MessageContent`)
  -- ``from_payload`` matches on JSON shape (``str`` vs ``list``); a ``None`` /
  absent value tolerates to the ``Text`` variant with an empty string (same
  forward-compat posture as the R202 :class:`ToolResultContent` untagged union).
- ``#[serde(rename="type")] r#type`` (:class:`SystemTextBlock`) -> ``type_`` field
  (wire key ``"type"``; renamed to avoid shadowing the Python builtin).

No I/O (``dict.get`` / ``isinstance`` are pure mappings). The
:class:`MessageContent` blocks variant recurses on the R202 :class:`ContentBlock`
union; the :class:`SystemParam` blocks variant recurses on
:class:`SystemTextBlock` (the standalone struct, NOT the ContentBlock variant).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler.message_bodies import (
    BlocksMessageContent,
    BlocksSystemParam,
    InputJsonDelta,
    MessageContent,
    SignatureDelta,
    StreamDelta,
    SystemParam,
    SystemTextBlock,
    TextDelta,
    TextMessageContent,
    TextSystemParam,
    ThinkingDelta,
)

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_twelve_symbols() -> None:
    """4 type families -> 12 re-exported symbols: SystemTextBlock leaf struct +
    SystemParam union base + 2 variants + MessageContent union base + 2 variants
    + StreamDelta union base + 4 variants."""
    import minimax_code.sampler.message_bodies as message_bodies

    assert len(message_bodies.__all__) == 12
    assert set(message_bodies.__all__) == {
        "BlocksMessageContent",
        "BlocksSystemParam",
        "InputJsonDelta",
        "MessageContent",
        "SignatureDelta",
        "StreamDelta",
        "SystemParam",
        "SystemTextBlock",
        "TextDelta",
        "TextMessageContent",
        "TextSystemParam",
        "ThinkingDelta",
    }


# ---------------------------------------------------------------------------
# SystemTextBlock: standalone text-block struct (the SystemParam.Blocks element).
# ---------------------------------------------------------------------------


def test_system_text_block_defaults() -> None:
    """An empty payload -> ``type="text"`` + ``text=""`` + ``cache_control=None``
    (all three fields default when absent)."""
    block = SystemTextBlock.from_payload({})
    assert block.type_ == "text"
    assert block.text == ""
    assert block.cache_control is None


def test_system_text_block_full_with_cache_control() -> None:
    """A complete payload parses ``cache_control`` through the R202
    :class:`CacheControl.from_payload` (the only R204 dependency)."""
    block = SystemTextBlock.from_payload(
        {"type": "text", "text": "be concise", "cache_control": {"type": "ephemeral"}}
    )
    assert block.type_ == "text"
    assert block.text == "be concise"
    assert block.cache_control is not None
    assert block.cache_control.type_ == "ephemeral"


def test_system_text_block_missing_text_defaults_empty() -> None:
    """A missing ``text`` tolerates to the empty string (the platform never fails
    a parse on a malformed system block)."""
    block = SystemTextBlock.from_payload({"type": "text"})
    assert block.text == ""


def test_system_text_block_cache_control_non_dict_ignored() -> None:
    """A non-dict ``cache_control`` (a stray string) is ignored -> ``None``
    rather than crash the parse."""
    block = SystemTextBlock.from_payload({"text": "x", "cache_control": "not-a-dict"})
    assert block.cache_control is None


def test_system_text_block_declares_slots() -> None:
    """``slots=True`` -> the class declares ``__slots__`` over its 3 fields + no
    per-instance ``__dict__``. Combined with ``frozen=True`` the attribute
    namespace is closed."""
    assert SystemTextBlock.__slots__ == ("type_", "text", "cache_control")
    block = SystemTextBlock.from_payload({})
    assert not hasattr(block, "__dict__")


# ---------------------------------------------------------------------------
# SystemParam: untagged 2-variant union (Text vs Blocks<TextBlock>).
# ---------------------------------------------------------------------------


def test_system_param_string_variant() -> None:
    """A JSON string -> :class:`TextSystemParam` (untagged ``String``)."""
    param = SystemParam.from_payload("you are grok")
    assert isinstance(param, TextSystemParam)
    assert param.text == "you are grok"
    assert isinstance(param, SystemParam)


def test_system_param_none_tolerates_to_empty_text() -> None:
    """A ``None`` / absent system prompt tolerates to the Text variant with an
    empty string (forward-compat -- the request's ``system`` field is optional)."""
    param = SystemParam.from_payload(None)
    assert isinstance(param, TextSystemParam)
    assert param.text == ""


def test_system_param_blocks_variant() -> None:
    """A JSON array -> :class:`BlocksSystemParam`, parsing each item through
    :meth:`SystemTextBlock.from_payload` (the standalone struct, NOT the R202
    ContentBlock variant)."""
    param = SystemParam.from_payload(
        [{"type": "text", "text": "rule one"}, {"type": "text", "text": "rule two"}]
    )
    assert isinstance(param, BlocksSystemParam)
    assert len(param.blocks) == 2
    assert all(isinstance(block, SystemTextBlock) for block in param.blocks)
    assert param.blocks[0].text == "rule one"
    assert param.blocks[1].text == "rule two"
    assert isinstance(param, SystemParam)


def test_system_param_blocks_variant_empty_list() -> None:
    """An empty array -> an empty blocks tuple (not the Text variant)."""
    param = SystemParam.from_payload([])
    assert isinstance(param, BlocksSystemParam)
    assert param.blocks == ()


def test_system_param_blocks_filters_non_dict_items() -> None:
    """Non-dict items in the array are skipped (a stray string / number cannot
    shape a system block) -- only dict items parse through SystemTextBlock."""
    param = SystemParam.from_payload([{"text": "keep"}, "skip-me", 42, {"text": "also keep"}])
    assert isinstance(param, BlocksSystemParam)
    assert len(param.blocks) == 2
    assert param.blocks[0].text == "keep"
    assert param.blocks[1].text == "also keep"


def test_system_param_invalid_shape_raises() -> None:
    """An untagged union has no fallback for an unrecognized JSON shape -- a
    number / dict / bool raises ``ValueError`` (mirrors serde's untagged
    try-each-variant failure)."""
    with pytest.raises(ValueError, match="system param must be string or list"):
        SystemParam.from_payload(42)
    with pytest.raises(ValueError, match="system param must be string or list"):
        SystemParam.from_payload({"unexpected": "dict"})
    with pytest.raises(ValueError, match="system param must be string or list"):
        SystemParam.from_payload(True)


# ---------------------------------------------------------------------------
# MessageContent: untagged 2-variant union (Text vs Blocks<ContentBlock>).
# ---------------------------------------------------------------------------


def test_message_content_string_variant() -> None:
    """A JSON string -> :class:`TextMessageContent` (untagged ``String``)."""
    content = MessageContent.from_payload("hello")
    assert isinstance(content, TextMessageContent)
    assert content.text == "hello"
    assert isinstance(content, MessageContent)


def test_message_content_none_tolerates_to_empty_text() -> None:
    """A ``None`` / absent content tolerates to the Text variant with an empty
    string (forward-compat)."""
    content = MessageContent.from_payload(None)
    assert isinstance(content, TextMessageContent)
    assert content.text == ""


def test_message_content_blocks_variant_recursive_content_block() -> None:
    """A JSON array -> :class:`BlocksMessageContent`, recursively parsing each
    item through the R202 :meth:`ContentBlock.from_payload` (a message may carry
    text / image / tool-use / tool-result / thinking blocks)."""
    content = MessageContent.from_payload(
        [{"type": "text", "text": "hi"}, {"type": "text", "text": "there"}]
    )
    assert isinstance(content, BlocksMessageContent)
    assert len(content.blocks) == 2
    # Each block is a R202 ContentBlock variant (the text variant here).
    from minimax_code.sampler.content_blocks import ContentBlock, TextBlock

    assert all(isinstance(block, ContentBlock) for block in content.blocks)
    assert isinstance(content.blocks[0], TextBlock)
    assert isinstance(content, MessageContent)


def test_message_content_blocks_variant_empty_list() -> None:
    content = MessageContent.from_payload([])
    assert isinstance(content, BlocksMessageContent)
    assert content.blocks == ()


def test_message_content_invalid_shape_raises() -> None:
    with pytest.raises(ValueError, match="message content must be string or list"):
        MessageContent.from_payload(3.14)
    with pytest.raises(ValueError, match="message content must be string or list"):
        MessageContent.from_payload({"no": "list"})


# ---------------------------------------------------------------------------
# StreamDelta: 4-variant tagged union (tag="type", rename_all="snake_case").
# ---------------------------------------------------------------------------


def test_stream_delta_text_delta() -> None:
    """``{"type":"text_delta","text":...}`` -> :class:`TextDelta`."""
    delta = StreamDelta.from_payload({"type": "text_delta", "text": "Hel"})
    assert isinstance(delta, TextDelta)
    assert delta.text == "Hel"
    assert isinstance(delta, StreamDelta)


def test_stream_delta_input_json_delta() -> None:
    """``{"type":"input_json_delta","partial_json":...}`` -> :class:`InputJsonDelta`
    (streamed tool-use argument assembly)."""
    delta = StreamDelta.from_payload({"type": "input_json_delta", "partial_json": '{"city":'})
    assert isinstance(delta, InputJsonDelta)
    assert delta.partial_json == '{"city":'
    assert isinstance(delta, StreamDelta)


def test_stream_delta_thinking_delta() -> None:
    """``{"type":"thinking_delta","thinking":...}`` -> :class:`ThinkingDelta`
    (an incremental extended-thinking chunk -- distinct from the R202 complete
    :class:`ThinkingBlock`)."""
    delta = StreamDelta.from_payload({"type": "thinking_delta", "thinking": "hmm"})
    assert isinstance(delta, ThinkingDelta)
    assert delta.thinking == "hmm"


def test_stream_delta_signature_delta() -> None:
    """``{"type":"signature_delta","signature":...}`` -> :class:`SignatureDelta`
    (the server-signed chunk that finalizes a thinking block)."""
    delta = StreamDelta.from_payload({"type": "signature_delta", "signature": "sig-abc"})
    assert isinstance(delta, SignatureDelta)
    assert delta.signature == "sig-abc"


def test_stream_delta_missing_payload_field_defaults_empty() -> None:
    """A present known ``type`` but missing payload field tolerates to the empty
    string (the platform never fails a stream on a malformed delta)."""
    delta = StreamDelta.from_payload({"type": "text_delta"})
    assert isinstance(delta, TextDelta)
    assert delta.text == ""


def test_stream_delta_unknown_type_raises() -> None:
    """grok tagged union has no catch-all -> an unknown ``type`` fails the parse
    (contrast the R201 StopReason catch-all, which must never fail a stream)."""
    with pytest.raises(ValueError, match="unknown stream delta type"):
        StreamDelta.from_payload({"type": "future_delta_kind"})


def test_stream_delta_missing_type_raises() -> None:
    """A missing ``type`` tag is treated as unknown -> ``ValueError``."""
    with pytest.raises(ValueError, match="unknown stream delta type"):
        StreamDelta.from_payload({"text": "no type tag"})


# ---------------------------------------------------------------------------
# Value semantics: frozen + slots + hashable + union base.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obj",
    [
        SystemTextBlock(type_="text", text="x"),
        TextSystemParam(text="x"),
        BlocksSystemParam(blocks=(SystemTextBlock(text="x"),)),
        TextMessageContent(text="x"),
        BlocksMessageContent(blocks=()),
        TextDelta(text="x"),
        InputJsonDelta(partial_json="x"),
        ThinkingDelta(thinking="x"),
        SignatureDelta(signature="x"),
    ],
)
def test_message_body_variants_are_frozen(obj: object) -> None:
    """``@dataclass(frozen=True)`` -> mutating the first slot raises (mirrors
    grok's immutable struct). Each parametrized variant carries >=1 field, so its
    ``__slots__`` is non-empty and the first slot is a valid mutation target."""
    field_name = next(iter(type(obj).__slots__))
    with pytest.raises(FrozenInstanceError):
        setattr(obj, field_name, "rewritten")  # type: ignore[misc]


def test_system_param_variants_share_base() -> None:
    """Both SystemParam variants are subclasses of the union base."""
    assert isinstance(TextSystemParam(text="x"), SystemParam)
    assert isinstance(BlocksSystemParam(blocks=()), SystemParam)


def test_message_content_variants_share_base() -> None:
    """Both MessageContent variants are subclasses of the union base."""
    assert isinstance(TextMessageContent(text="x"), MessageContent)
    assert isinstance(BlocksMessageContent(blocks=()), MessageContent)


def test_stream_delta_variants_share_base() -> None:
    """All 4 StreamDelta variants are subclasses of the union base."""
    assert isinstance(TextDelta(text="x"), StreamDelta)
    assert isinstance(InputJsonDelta(partial_json="x"), StreamDelta)
    assert isinstance(ThinkingDelta(thinking="x"), StreamDelta)
    assert isinstance(SignatureDelta(signature="x"), StreamDelta)


def test_blocks_variants_are_hashable_via_frozen_tuples() -> None:
    """The blocks-carrying variants store a ``tuple`` (frozen -> hashable), so the
    whole variant is hashable + equal-by-value (usable as dict keys, mirrors
    grok's immutable ``Vec``-carrying struct)."""
    block = SystemTextBlock(text="rule")
    a = BlocksSystemParam(blocks=(block,))
    b = BlocksSystemParam(blocks=(SystemTextBlock(text="rule"),))
    assert a == b
    assert hash(a) == hash(b)

    empty_a = BlocksMessageContent(blocks=())
    empty_b = BlocksMessageContent(blocks=())
    assert empty_a == empty_b
    assert hash(empty_a) == hash(empty_b)
