"""Tests for sampler.chat_completion_mid (R207,
``xai-grok-sampling-types`` ``types.rs``).

Covers the second ``types.rs`` slice -- 11 middle-layer leaves of the
OpenAI-compatible ChatCompletion type family that compose the R206 atomic
leaves into request/response body shapes:

- :class:`ChatContentBlock` (2-variant tagged union, ``tag="type"``) -- the
  ``text`` vs ``image_url`` block inside a :class:`ChatMessageContent` blocks
  list. Strict tagged-union parse (unknown ``type`` / non-dict raises -- no
  catch-all, mirrors serde; contrast the R201 StopReason catch-all).
- :class:`ChatMessageContent` (untagged 2-variant union, renamed from grok
  ``MessageContent``) -- ``Text(String)`` vs ``Blocks(Vec<ChatContentBlock>)``.
  ``from_payload`` matches on JSON shape (``str`` / ``None`` -> Text,
  ``list`` -> Blocks); carries :meth:`is_empty` / :meth:`blocks`.
- :class:`ToolChoice` (untagged 2-variant union) -- ``Preset(String)`` vs
  ``Function { kind, function }``; ``from_payload`` matches on JSON shape
  (``str`` -> Preset, ``dict`` -> Function); carries the ``auto`` / ``none`` /
  ``required`` / ``function`` classmethod constructors.
- :class:`ToolCallRequest` (struct) -- ``id`` / ``kind`` / ``function``; carries
  the ``function`` constructor + the copy-on-write ``with_id`` builder.
- :class:`ChatUsage` (struct, renamed from grok ``Usage``) -- the 6-field
  token-usage counter (3 bare counters + 2 optional nested breakdowns + the
  xAI ``cost_in_usd_ticks`` billing extension).

Naming: the 3 union families that clash with the Anthropic Messages API peers
carry a ``Chat`` prefix to dodge the barrel collision --
:class:`ChatContentBlock` vs R202 :class:`ContentBlock`,
:class:`ChatMessageContent` vs R204 :class:`MessageContent`,
:class:`ChatUsage` vs R201 :class:`MessagesUsage`. :class:`ToolChoice` /
:class:`ToolCallRequest` are new (distinct from R203 :class:`ToolChoiceParam`
/ R206 :class:`ToolCallFunction`).

No I/O (``dict.get`` / ``isinstance`` are pure mappings).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler.chat_completion_leaves import (
    ImageUrl,
    ToolCallFunction,
    ToolChoiceFunction,
    ToolType,
)
from minimax_code.sampler.chat_completion_mid import (
    ChatBlocksContent,
    ChatContentBlock,
    ChatImageUrlBlock,
    ChatMessageContent,
    ChatTextBlock,
    ChatTextContent,
    ChatUsage,
    FunctionToolChoice,
    PresetToolChoice,
    ToolCallRequest,
    ToolChoice,
)

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_eleven_symbols() -> None:
    """5 union/struct families = 11 re-exported symbols: 2 tagged-union
    variants (ChatTextBlock / ChatImageUrlBlock) + 2 untagged-union variants
    (ChatTextContent / ChatBlocksContent) + 2 tool-choice variants
    (PresetToolChoice / FunctionToolChoice) + 4 union/struct bases
    (ChatContentBlock / ChatMessageContent / ToolChoice / ToolCallRequest /
    ChatUsage)."""
    import minimax_code.sampler.chat_completion_mid as mid

    assert len(mid.__all__) == 11
    assert set(mid.__all__) == {
        "ChatBlocksContent",
        "ChatContentBlock",
        "ChatImageUrlBlock",
        "ChatMessageContent",
        "ChatTextBlock",
        "ChatTextContent",
        "ChatUsage",
        "FunctionToolChoice",
        "PresetToolChoice",
        "ToolCallRequest",
        "ToolChoice",
    }


# ---------------------------------------------------------------------------
# ChatContentBlock: 2-variant tagged union (tag="type").
# ---------------------------------------------------------------------------


def test_chat_content_block_text_variant() -> None:
    """``{"type":"text","text":...}`` -> :class:`ChatTextBlock` (inline text)."""
    block = ChatContentBlock.from_payload({"type": "text", "text": "hello"})
    assert isinstance(block, ChatTextBlock)
    assert block.text == "hello"


def test_chat_content_block_text_missing_field_defaults_empty() -> None:
    """A missing ``text`` key tolerates to the empty string (forward-compat --
    the platform never fails a parse on a sparse block)."""
    block = ChatContentBlock.from_payload({"type": "text"})
    assert isinstance(block, ChatTextBlock)
    assert block.text == ""


def test_chat_content_block_image_url_variant() -> None:
    """``{"type":"image_url","image_url":{...}}`` -> :class:`ChatImageUrlBlock`
    wrapping an :class:`ImageUrl` (recursively parsed)."""
    block = ChatContentBlock.from_payload(
        {"type": "image_url", "image_url": {"url": "https://example.com/x.png"}}
    )
    assert isinstance(block, ChatImageUrlBlock)
    assert block.image_url == ImageUrl(url="https://example.com/x.png")


def test_chat_content_block_image_url_missing_payload_tolerates_empty() -> None:
    """A missing / empty ``image_url`` payload -> an empty :class:`ImageUrl`
    (delegated to :meth:`ImageUrl.from_payload`'s tolerant default)."""
    block = ChatContentBlock.from_payload({"type": "image_url", "image_url": {}})
    assert isinstance(block, ChatImageUrlBlock)
    assert block.image_url == ImageUrl(url="")


def test_chat_content_block_unknown_type_raises() -> None:
    """grok enum has no ``#[serde(other)]`` catch-all -> an unknown ``type``
    raises (mirrors serde's strict tagged-union parse)."""
    with pytest.raises(ValueError, match="unknown chat content block type"):
        ChatContentBlock.from_payload({"type": "audio"})
    with pytest.raises(ValueError, match="unknown chat content block type"):
        ChatContentBlock.from_payload({"type": None})


def test_chat_content_block_non_dict_raises() -> None:
    """A non-dict payload raises (the tagged union parses wire objects only)."""
    with pytest.raises(ValueError, match="chat content block must be a dict"):
        ChatContentBlock.from_payload("text")
    with pytest.raises(ValueError, match="chat content block must be a dict"):
        ChatContentBlock.from_payload(None)


# ---------------------------------------------------------------------------
# ChatMessageContent: untagged 2-variant union (Text vs Blocks).
# ---------------------------------------------------------------------------


def test_chat_message_content_string_is_text_variant() -> None:
    """A JSON string -> :class:`ChatTextContent` (untagged ``String``)."""
    content = ChatMessageContent.from_payload("hello")
    assert isinstance(content, ChatTextContent)
    assert content.text == "hello"


def test_chat_message_content_none_tolerates_to_empty_text() -> None:
    """A ``None`` / absent content -> :class:`ChatTextContent` with the empty
    string (forward-compat posture, mirrors the R204 :class:`MessageContent`
    untagged union)."""
    assert isinstance(ChatMessageContent.from_payload(None), ChatTextContent)
    assert ChatMessageContent.from_payload(None).text == ""


def test_chat_message_content_list_is_blocks_variant() -> None:
    """A JSON array -> :class:`ChatBlocksContent` (untagged
    ``Vec<ChatContentBlock>``), each item recursively parsed through
    :meth:`ChatContentBlock.from_payload`."""
    content = ChatMessageContent.from_payload(
        [
            {"type": "text", "text": "a"},
            {"type": "image_url", "image_url": {"url": "u"}},
        ]
    )
    assert isinstance(content, ChatBlocksContent)
    assert len(content.blocks) == 2
    assert isinstance(content.blocks[0], ChatTextBlock)
    assert content.blocks[0].text == "a"
    assert isinstance(content.blocks[1], ChatImageUrlBlock)


def test_chat_message_content_empty_list_is_empty_blocks() -> None:
    """An empty JSON array -> :class:`ChatBlocksContent` with an empty tuple."""
    content = ChatMessageContent.from_payload([])
    assert isinstance(content, ChatBlocksContent)
    assert content.blocks == ()


def test_chat_message_content_list_skips_non_dict_items() -> None:
    """Non-dict items in the array are skipped (the list comprehension guards
    ``isinstance(item, dict)`` -- a stray string / number in the blocks list is
    dropped rather than crashing the parse)."""
    content = ChatMessageContent.from_payload(
        [{"type": "text", "text": "keep"}, "junk", 42]
    )
    assert isinstance(content, ChatBlocksContent)
    assert len(content.blocks) == 1


def test_chat_message_content_other_shape_raises() -> None:
    """A non-string / non-list / non-None payload raises (the untagged union
    has no matching variant)."""
    with pytest.raises(ValueError, match="chat message content must be string or list"):
        ChatMessageContent.from_payload(42)
    with pytest.raises(ValueError, match="chat message content must be string or list"):
        ChatMessageContent.from_payload({"type": "text"})


def test_chat_message_content_is_empty_text() -> None:
    """``MessageContent::is_empty``: a Text is empty iff its string is empty."""
    assert ChatTextContent(text="").is_empty() is True
    assert ChatTextContent(text="x").is_empty() is False


def test_chat_message_content_is_empty_blocks() -> None:
    """``MessageContent::is_empty``: a Blocks is empty iff its list is empty."""
    assert ChatBlocksContent(blocks=()).is_empty() is True
    block = ChatTextBlock(text="x")
    assert ChatBlocksContent(blocks=(block,)).is_empty() is False


def test_chat_message_content_to_blocks_of_blocks_returns_self() -> None:
    """``MessageContent::blocks`` (mirrored as :meth:`to_blocks`): a Blocks value
    yields its list as-is. Renamed ``blocks`` -> ``to_blocks`` because the
    :class:`ChatBlocksContent` subclass ``blocks`` slot would shadow a same-named
    base method (``content.blocks`` resolves to the tuple, not the method)."""
    blocks = (ChatTextBlock(text="a"), ChatImageUrlBlock(image_url=ImageUrl()))
    content = ChatBlocksContent(blocks=blocks)
    assert content.to_blocks() == blocks


def test_chat_message_content_to_blocks_of_text_wraps_into_single_block() -> None:
    """``MessageContent::blocks`` (mirrored as :meth:`to_blocks`): a Text value
    is wrapped into a single :class:`ChatTextBlock` (mirrors grok's
    ``vec![ChatContentBlock::Text { text }]``)."""
    content = ChatTextContent(text="hi")
    result = content.to_blocks()
    assert len(result) == 1
    assert isinstance(result[0], ChatTextBlock)
    assert result[0].text == "hi"


# ---------------------------------------------------------------------------
# ToolChoice: untagged 2-variant union (Preset vs Function).
# ---------------------------------------------------------------------------


def test_tool_choice_string_is_preset_variant() -> None:
    """A JSON string -> :class:`PresetToolChoice` (untagged ``String`` -- the
    ``"auto"`` / ``"none"`` / ``"required"`` presets)."""
    choice = ToolChoice.from_payload("auto")
    assert isinstance(choice, PresetToolChoice)
    assert choice.value == "auto"


def test_tool_choice_dict_is_function_variant() -> None:
    """A JSON object -> :class:`FunctionToolChoice` (untagged ``Function {
    kind, function }``); ``kind`` parses from the wire ``type`` (defaulting to
    ``function``), ``function`` recurses through
    :meth:`ToolChoiceFunction.from_payload`."""
    choice = ToolChoice.from_payload(
        {"type": "function", "function": {"name": "get_weather"}}
    )
    assert isinstance(choice, FunctionToolChoice)
    assert choice.kind is ToolType.FUNCTION
    assert choice.function == ToolChoiceFunction(name="get_weather")


def test_tool_choice_dict_missing_type_defaults_function() -> None:
    """A dict without the ``type`` key defaults the kind to ``function``
    (forward-compat -- the discriminator is implied when a function payload is
    present)."""
    choice = ToolChoice.from_payload({"function": {"name": "f"}})
    assert isinstance(choice, FunctionToolChoice)
    assert choice.kind is ToolType.FUNCTION


def test_tool_choice_other_shape_raises() -> None:
    """A non-string / non-dict payload raises (the untagged union has no
    matching variant)."""
    with pytest.raises(ValueError, match="tool choice must be string or dict"):
        ToolChoice.from_payload(42)
    with pytest.raises(ValueError, match="tool choice must be string or dict"):
        ToolChoice.from_payload(None)


def test_tool_choice_auto_preset() -> None:
    """``ToolChoice::auto`` -> ``PresetToolChoice("auto")``."""
    choice = ToolChoice.auto()
    assert isinstance(choice, PresetToolChoice)
    assert choice.value == "auto"


def test_tool_choice_none_preset() -> None:
    """``ToolChoice::none`` -> ``PresetToolChoice("none")`` (``none`` is a
    method name here -- ``None`` is the Python keyword)."""
    choice = ToolChoice.none()
    assert isinstance(choice, PresetToolChoice)
    assert choice.value == "none"


def test_tool_choice_required_preset() -> None:
    """``ToolChoice::required`` -> ``PresetToolChoice("required")``."""
    choice = ToolChoice.required()
    assert isinstance(choice, PresetToolChoice)
    assert choice.value == "required"


def test_tool_choice_function_constructor() -> None:
    """``ToolChoice::function(name)`` -> ``FunctionToolChoice`` with
    ``kind = Function`` + a named :class:`ToolChoiceFunction`."""
    choice = ToolChoice.function("get_weather")
    assert isinstance(choice, FunctionToolChoice)
    assert choice.kind is ToolType.FUNCTION
    assert choice.function == ToolChoiceFunction(name="get_weather")


# ---------------------------------------------------------------------------
# ToolCallRequest: an assistant message's emitted tool call (struct).
# ---------------------------------------------------------------------------


def test_tool_call_request_full_payload() -> None:
    """A full payload parses ``id`` / ``kind`` / ``function`` (recursing into
    :meth:`ToolCallFunction.from_payload` for the nested function)."""
    req = ToolCallRequest.from_payload(
        {"id": "call_1", "type": "function", "function": {"name": "f", "arguments": "{}"}}
    )
    assert req.id == "call_1"
    assert req.kind is ToolType.FUNCTION
    assert req.function == ToolCallFunction(name="f", arguments="{}")


def test_tool_call_request_missing_keys_default() -> None:
    """A payload missing ``id`` / ``type`` defaults ``id=None`` /
    ``kind=Function`` (forward-compat)."""
    req = ToolCallRequest.from_payload({"function": {"name": "f"}})
    assert req.id is None
    assert req.kind is ToolType.FUNCTION
    assert req.function.name == "f"


def test_tool_call_request_non_dict_defaults() -> None:
    """A null / non-dict payload -> the all-default instance (no id, Function
    kind, an empty :class:`ToolCallFunction`)."""
    req = ToolCallRequest.from_payload(None)
    assert req == ToolCallRequest()
    assert req.id is None
    assert req.kind is ToolType.FUNCTION
    assert req.function == ToolCallFunction()
    assert ToolCallRequest.from_payload("nope") == ToolCallRequest()


def test_tool_call_request_with_function_constructor() -> None:
    """``ToolCallRequest::function(name, arguments)`` -> no id, ``kind =
    Function``, a fresh :class:`ToolCallFunction` carrying name + arguments.
    Mirrored as ``with_function`` (renamed from grok's ``function`` because
    Python's field/method namespaces collide -- the struct has a ``function``
    field)."""
    req = ToolCallRequest.with_function("search", '{"q": "x"}')
    assert req.id is None
    assert req.kind is ToolType.FUNCTION
    assert req.function == ToolCallFunction(name="search", arguments='{"q": "x"}')


def test_tool_call_request_with_id_is_copy_on_write() -> None:
    """``ToolCallRequest::with_id(self, id)`` returns a NEW instance with the id
    set; the original is untouched (``frozen=True`` -> copy-on-write builder)."""
    original = ToolCallRequest.with_function("f", "{}")
    stamped = original.with_id("call_9")
    assert stamped is not original
    assert stamped.id == "call_9"
    # original untouched (frozen -> the builder cannot mutate in place)
    assert original.id is None
    # the rest of the fields are carried over
    assert stamped.kind is original.kind
    assert stamped.function == original.function


# ---------------------------------------------------------------------------
# ChatUsage: ChatCompletion token-usage counter (struct, renamed from Usage).
# ---------------------------------------------------------------------------


def test_chat_usage_full_payload() -> None:
    """A full payload parses all 6 fields: 3 bare counters + 2 nested breakdowns
    (recursing through their ``from_payload``) + the xAI ``cost_in_usd_ticks``
    billing extension."""
    usage = ChatUsage.from_payload(
        {
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_tokens_details": {"cached_tokens": 10, "audio_tokens": 2},
            "completion_tokens_details": {"reasoning_tokens": 20, "audio_tokens": 1},
            "cost_in_usd_ticks": 12345,
        }
    )
    assert usage.prompt_tokens == 100
    assert usage.completion_tokens == 50
    assert usage.total_tokens == 150
    assert usage.prompt_tokens_details == ChatUsage.from_payload(
        {"prompt_tokens_details": {"cached_tokens": 10, "audio_tokens": 2}}
    ).prompt_tokens_details
    assert usage.completion_tokens_details is not None
    assert usage.completion_tokens_details.reasoning_tokens == 20
    assert usage.cost_in_usd_ticks == 12345


def test_chat_usage_empty_payload_defaults() -> None:
    """An empty payload -> all counters 0, both breakdowns ``None``,
    ``cost_in_usd_ticks`` ``None`` (mirrors grok's ``Option::None``)."""
    usage = ChatUsage.from_payload({})
    assert usage == ChatUsage()
    assert usage.prompt_tokens == 0
    assert usage.completion_tokens == 0
    assert usage.total_tokens == 0
    assert usage.prompt_tokens_details is None
    assert usage.completion_tokens_details is None
    assert usage.cost_in_usd_ticks is None


def test_chat_usage_non_dict_defaults() -> None:
    """A null / non-dict payload -> the all-default instance."""
    assert ChatUsage.from_payload(None) == ChatUsage()
    assert ChatUsage.from_payload("nope") == ChatUsage()


def test_chat_usage_non_dict_breakdown_tolerates_none() -> None:
    """A non-dict ``prompt_tokens_details`` / ``completion_tokens_details``
    value stays ``None`` (mirrors grok's ``Option::None`` when the nested
    object is malformed)."""
    usage = ChatUsage.from_payload(
        {"prompt_tokens_details": "broken", "completion_tokens_details": 42}
    )
    assert usage.prompt_tokens_details is None
    assert usage.completion_tokens_details is None


# ---------------------------------------------------------------------------
# Naming isolation: Chat* peers never collide with the Anthropic Messages API
# peers in the package barrel.
# ---------------------------------------------------------------------------


def test_chat_message_content_is_distinct_from_message_content() -> None:
    """``types.rs`` ``MessageContent`` (renamed :class:`ChatMessageContent`,
    OpenAI ChatCompletion) is a different class from the R204
    :class:`MessageContent` (Anthropic Messages API) -- the two never collide in
    the barrel. The Blocks variant carries :class:`ChatContentBlock` (text /
    image_url) vs the R204 peer's :class:`ContentBlock` (5 Anthropic variants)."""
    from minimax_code.sampler.message_bodies import MessageContent

    assert ChatMessageContent is not MessageContent
    # ChatMessageContent has the Chat* block variants; MessageContent does not
    assert hasattr(ChatMessageContent, "from_payload")
    assert MessageContent is not ChatMessageContent


def test_chat_usage_is_distinct_from_messages_usage() -> None:
    """``types.rs`` ``Usage`` (renamed :class:`ChatUsage`, OpenAI
    ChatCompletion) is a different class from the R201 :class:`MessagesUsage`
    (Anthropic Messages API) -- the two never collide in the barrel.
    :class:`ChatUsage` carries the xAI ``cost_in_usd_ticks`` billing extension;
    :class:`MessagesUsage` does not."""
    from minimax_code.sampler.messages import MessagesUsage

    assert ChatUsage is not MessagesUsage
    assert hasattr(ChatUsage, "cost_in_usd_ticks")
    assert not hasattr(MessagesUsage, "cost_in_usd_ticks")


def test_tool_choice_is_distinct_from_tool_choice_param() -> None:
    """:class:`ToolChoice` (OpenAI ChatCompletion untagged Preset/Function
    union) is a different class from the R203 :class:`ToolChoiceParam`
    (Anthropic Messages API tagged union) -- the two never collide in the
    barrel. :class:`ToolChoice` carries the ``auto`` / ``none`` / ``required``
    presets; :class:`ToolChoiceParam` carries ``AutoToolChoiceParam`` /
    ``AnyToolChoiceParam`` / ``NamedToolChoiceParam`` variants."""
    from minimax_code.sampler.request_params import ToolChoiceParam

    assert ToolChoice is not ToolChoiceParam
    assert hasattr(ToolChoice, "auto")
    assert not hasattr(ToolChoiceParam, "auto")


def test_chat_content_block_is_distinct_from_content_block() -> None:
    """:class:`ChatContentBlock` (OpenAI 2-variant text/image_url union) is a
    different class from the R202 :class:`ContentBlock` (Anthropic 5-variant
    Text/Image/ToolUse/ToolResult/Thinking union) -- the two never collide in
    the barrel."""
    from minimax_code.sampler.content_blocks import ContentBlock

    assert ChatContentBlock is not ContentBlock


# ---------------------------------------------------------------------------
# Value semantics: frozen + slots + hashable.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obj",
    [
        ChatTextBlock(text="x"),
        ChatImageUrlBlock(image_url=ImageUrl(url="u")),
        ChatTextContent(text="x"),
        ChatBlocksContent(blocks=(ChatTextBlock(text="a"),)),
        PresetToolChoice(value="auto"),
        FunctionToolChoice(kind=ToolType.FUNCTION, function=ToolChoiceFunction(name="f")),
        ToolCallRequest(id="c1", function=ToolCallFunction(name="f", arguments="{}")),
        ChatUsage(prompt_tokens=10, total_tokens=20),
    ],
)
def test_mid_structs_are_frozen(obj: object) -> None:
    """``@dataclass(frozen=True)`` -> mutating the first declared slot raises
    (mirrors grok's immutable struct). Uses ``setattr`` with a variable field
    name (the first declared slot) so the raise is driven by the dataclass
    ``__setattr__`` rather than a slots-name lookup -- a frozen+slots dataclass
    raises ``FrozenInstanceError`` on a real field but a ``TypeError`` on an
    out-of-slots name (so the target must be a declared field)."""
    field_name = next(iter(type(obj).__slots__))
    with pytest.raises(FrozenInstanceError):
        setattr(obj, field_name, "rewritten")  # type: ignore[misc]


def test_mid_structs_are_hashable_and_equal() -> None:
    """``frozen=True`` -> hashable + equal-by-value (usable as dict keys,
    mirrors grok's immutable struct)."""
    assert ChatTextBlock(text="x") == ChatTextBlock(text="x")
    assert hash(ChatTextBlock(text="x")) == hash(ChatTextBlock(text="x"))
    assert ChatUsage(prompt_tokens=1) == ChatUsage(prompt_tokens=1)
    assert hash(ChatUsage(prompt_tokens=1)) == hash(ChatUsage(prompt_tokens=1))
    assert PresetToolChoice(value="auto") == PresetToolChoice(value="auto")
    assert hash(PresetToolChoice(value="auto")) == hash(PresetToolChoice(value="auto"))
    req = ToolCallRequest(id="c1", function=ToolCallFunction(name="f", arguments="{}"))
    assert req == ToolCallRequest(id="c1", function=ToolCallFunction(name="f", arguments="{}"))
    assert hash(req) == hash(
        ToolCallRequest(id="c1", function=ToolCallFunction(name="f", arguments="{}"))
    )
