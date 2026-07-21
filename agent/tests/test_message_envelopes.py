"""Tests for sampler.message_envelopes (R205, ``xai-grok-sampling-types`` ``messages.rs``).

Covers the 4 outermost wire containers that close the ``messages.rs`` wire-type
layer: :class:`Message` (a single turn) + :class:`MessagesResponse` (the
non-streaming reply) + :class:`MessagesRequest` (the ``#[derive(Default)]``
request body) + the :class:`MessageStreamEvent` 8-variant tagged-union wrapper.
Mirrors the grok wire shapes:

- plain struct (no Default derive) (:class:`MessagesResponse`) -> frozen+slots
  with a tolerant ``from_payload``; the wire ``type`` field maps to ``type_``.
- ``#[derive(Default)]`` (:class:`MessagesRequest`) -> a ``default()`` classmethod
  + ``from_payload`` that parses each nested knob only when present (``Option``
  stays ``None`` for a missing/null key).
- ``#[serde(tag="type", rename_all="snake_case")] enum``
  (:class:`MessageStreamEvent`) -- strict, no catch-all (unknown ``type`` raises,
  contrasting the R201 :class:`StopReason` catch-all which must never fail a
  terminal stream). Fieldless variants (``message_stop`` / ``ping``) map to
  fieldless subclasses.
- ``Vec<T>`` -> ``tuple[...]`` (frozen -> hashable); :class:`Message` role falls
  back to ``user`` on an unknown role string (forward-compat -- a future role
  never fails the parse, same posture as the R201 catch-all).

No I/O (``dict.get`` / ``isinstance`` are pure mappings). The containers recurse
on every R201-R204 leaf landed so far.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler.content_blocks import TextBlock
from minimax_code.sampler.message_bodies import (
    BlocksMessageContent,
    TextSystemParam,
)
from minimax_code.sampler.message_envelopes import (
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    Message,
    MessageDeltaEvent,
    MessagesRequest,
    MessagesResponse,
    MessageStartEvent,
    MessageStopEvent,
    MessageStreamEvent,
    PingEvent,
    StreamErrorEvent,
)
from minimax_code.sampler.messages import (
    EndTurn,
    MessageDeltaBody,
    MessageDeltaUsage,
    StreamError,
    UnknownStopReason,
)
from minimax_code.sampler.request_params import (
    AutoToolChoiceParam,
    DisabledThinkingConfig,
    MessageRole,
    Metadata,
    OutputConfig,
    ToolParam,
)

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_twelve_symbols() -> None:
    """4 type families -> 12 re-exported symbols: Message leaf struct +
    MessagesResponse leaf struct + MessagesRequest leaf struct +
    MessageStreamEvent union base + 8 variants."""
    import minimax_code.sampler.message_envelopes as envelopes

    assert len(envelopes.__all__) == 12
    assert set(envelopes.__all__) == {
        "ContentBlockDeltaEvent",
        "ContentBlockStartEvent",
        "ContentBlockStopEvent",
        "Message",
        "MessageDeltaEvent",
        "MessageStartEvent",
        "MessageStopEvent",
        "MessageStreamEvent",
        "MessagesRequest",
        "MessagesResponse",
        "PingEvent",
        "StreamErrorEvent",
    }


# ---------------------------------------------------------------------------
# Message: a single conversation turn (role + content).
# ---------------------------------------------------------------------------


def test_message_defaults_user_role_and_empty_text() -> None:
    """An empty payload -> ``role=user`` + empty-text content (both fields
    default when absent)."""
    msg = Message.from_payload({})
    assert msg.role == MessageRole.USER
    assert msg.content.text == ""


def test_message_assistant_role_with_string_content() -> None:
    """A known role string + string content parses through the R203 MessageRole +
    the R204 MessageContent Text variant."""
    msg = Message.from_payload({"role": "assistant", "content": "hi"})
    assert msg.role == MessageRole.ASSISTANT
    assert msg.content.text == "hi"


def test_message_unknown_role_falls_back_to_user() -> None:
    """An unknown role string (a future role not in the enum) falls back to
    ``user`` -- forward-compat, the platform never fails a parse on a new role
    (same posture as the R201 StopReason catch-all)."""
    msg = Message.from_payload({"role": "future_role", "content": "x"})
    assert msg.role == MessageRole.USER
    assert msg.content.text == "x"


def test_message_blocks_content_recursive_content_block() -> None:
    """A list content -> :class:`BlocksMessageContent`, recursively parsing each
    item through the R202 :meth:`ContentBlock.from_payload` (the element shape of
    a :class:`MessagesRequest` messages list)."""
    msg = Message.from_payload(
        {"role": "user", "content": [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]}
    )
    assert isinstance(msg.content, BlocksMessageContent)
    assert len(msg.content.blocks) == 2
    assert all(isinstance(block, TextBlock) for block in msg.content.blocks)
    assert msg.content.blocks[0].text == "a"
    assert msg.content.blocks[1].text == "b"


# ---------------------------------------------------------------------------
# MessagesResponse: the non-streaming POST /v1/messages reply.
# ---------------------------------------------------------------------------


def test_messages_response_defaults() -> None:
    """An empty payload -> ``id=""`` / ``type_="message"`` / ``role="assistant"``
    / empty content / ``model=""`` / ``stop_reason=None`` / zero usage."""
    resp = MessagesResponse.from_payload({})
    assert resp.id == ""
    assert resp.type_ == "message"
    assert resp.role == "assistant"
    assert resp.content == ()
    assert resp.model == ""
    assert resp.stop_reason is None
    assert resp.usage.input_tokens == 0
    assert resp.usage.output_tokens == 0


def test_messages_response_full_payload() -> None:
    """A complete reply parses ``content`` through the R202 ContentBlock union +
    ``stop_reason`` through the R201 catch-all + ``usage`` through MessagesUsage."""
    resp = MessagesResponse.from_payload(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "hi"}],
            "model": "grok-1",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 3, "output_tokens": 5},
        }
    )
    assert resp.id == "msg_1"
    assert resp.model == "grok-1"
    assert len(resp.content) == 1
    assert isinstance(resp.content[0], TextBlock)
    assert isinstance(resp.stop_reason, EndTurn)
    assert resp.usage.input_tokens == 3
    assert resp.usage.output_tokens == 5


def test_messages_response_unknown_stop_reason_catch_all() -> None:
    """A future stop reason parses through the R201 catch-all ->
    :class:`UnknownStopReason` (the platform never fails a terminal parse)."""
    resp = MessagesResponse.from_payload({"stop_reason": "future_reason"})
    assert isinstance(resp.stop_reason, UnknownStopReason)
    assert resp.stop_reason.value == "future_reason"


def test_messages_response_content_filters_non_dict_items() -> None:
    """Non-dict items in the ``content`` array are skipped (a stray string /
    number cannot shape a content block) -- only dict items parse through the
    R202 ContentBlock union."""
    resp = MessagesResponse.from_payload(
        {"content": [{"type": "text", "text": "ok"}, "skip", 42, {"type": "text", "text": "ok2"}]}
    )
    assert len(resp.content) == 2
    assert resp.content[0].text == "ok"
    assert resp.content[1].text == "ok2"


def test_messages_response_non_dict_usage_defaults_zero() -> None:
    """A non-dict ``usage`` (a stray string) is ignored -> the all-zero default
    rather than crash the parse."""
    resp = MessagesResponse.from_payload({"usage": "not-a-dict"})
    assert resp.usage.input_tokens == 0
    assert resp.usage.output_tokens == 0


# ---------------------------------------------------------------------------
# MessagesRequest: the POST /v1/messages request body (#[derive(Default)]).
# ---------------------------------------------------------------------------


def test_messages_request_default_is_all_none_options() -> None:
    """``default()`` mirrors ``#[derive(Default)]``: ``model=""`` / ``messages=()``
    / ``max_tokens=0`` and every one of the 11 optional knobs ``None``."""
    req = MessagesRequest.default()
    assert req.model == ""
    assert req.messages == ()
    assert req.max_tokens == 0
    assert req.system is None
    assert req.tools is None
    assert req.tool_choice is None
    assert req.temperature is None
    assert req.top_p is None
    assert req.top_k is None
    assert req.stream is None
    assert req.stop_sequences is None
    assert req.thinking is None
    assert req.output_config is None
    assert req.metadata is None


def test_messages_request_from_payload_empty_matches_default() -> None:
    """An empty payload -> the same shape as ``default()`` (every Option stays
    ``None`` for a missing key, mirroring serde's ``Option``)."""
    req = MessagesRequest.from_payload({})
    assert req.model == ""
    assert req.messages == ()
    assert req.max_tokens == 0
    assert req.system is None
    assert req.tools is None
    assert req.tool_choice is None
    assert req.thinking is None
    assert req.output_config is None
    assert req.metadata is None
    assert req.stop_sequences is None


def test_messages_request_messages_recursive_and_filters_non_dict() -> None:
    """The ``messages`` list recursively parses each item through
    :meth:`Message.from_payload`; non-dict items are skipped."""
    req = MessagesRequest.from_payload(
        {
            "messages": [
                {"role": "user", "content": "hi"},
                "skip",
                {"role": "assistant", "content": "yo"},
            ]
        }
    )
    assert len(req.messages) == 2
    assert req.messages[0].role == MessageRole.USER
    assert req.messages[0].content.text == "hi"
    assert req.messages[1].role == MessageRole.ASSISTANT
    assert req.messages[1].content.text == "yo"


def test_messages_request_nested_optional_knobs_parse() -> None:
    """Each nested knob parses through its R203/R204 leaf ``from_payload`` when a
    value is on the wire: ``system`` (untagged union) / ``tools`` (tuple of
    ToolParam) / ``tool_choice`` (tagged union) / ``thinking`` (tagged union) /
    ``output_config`` / ``metadata``."""
    req = MessagesRequest.from_payload(
        {
            "system": "be concise",
            "tools": [{"name": "t1"}],
            "tool_choice": {"type": "auto"},
            "thinking": {"type": "disabled"},
            "output_config": {},
            "metadata": {"user_id": "u1"},
        }
    )
    assert isinstance(req.system, TextSystemParam)
    assert req.system.text == "be concise"
    assert isinstance(req.tools, tuple)
    assert len(req.tools) == 1
    assert isinstance(req.tools[0], ToolParam)
    assert req.tools[0].name == "t1"
    assert isinstance(req.tool_choice, AutoToolChoiceParam)
    assert isinstance(req.thinking, DisabledThinkingConfig)
    assert isinstance(req.output_config, OutputConfig)
    assert isinstance(req.metadata, Metadata)
    assert req.metadata.user_id == "u1"


def test_messages_request_scalar_knobs_and_tuple_sequences() -> None:
    """The scalar knobs (``temperature`` / ``top_p`` / ``top_k`` / ``stream``)
    pass through verbatim; ``stop_sequences`` becomes a tuple when list-shaped."""
    req = MessagesRequest.from_payload(
        {
            "model": "grok-1",
            "max_tokens": 1024,
            "temperature": 0.7,
            "top_p": 0.9,
            "top_k": 40,
            "stream": True,
            "stop_sequences": ["a", "b"],
        }
    )
    assert req.model == "grok-1"
    assert req.max_tokens == 1024
    assert req.temperature == 0.7
    assert req.top_p == 0.9
    assert req.top_k == 40
    assert req.stream is True
    assert req.stop_sequences == ("a", "b")


# ---------------------------------------------------------------------------
# MessageStreamEvent: 8-variant tagged union (tag="type", rename_all="snake_case").
# ---------------------------------------------------------------------------


def test_message_stream_event_message_start() -> None:
    """``{"type":"message_start","message":{...}}`` -> :class:`MessageStartEvent`
    wrapping a :class:`MessagesResponse`."""
    ev = MessageStreamEvent.from_payload({"type": "message_start", "message": {"id": "msg_1"}})
    assert isinstance(ev, MessageStartEvent)
    assert isinstance(ev.message, MessagesResponse)
    assert ev.message.id == "msg_1"


def test_message_stream_event_message_delta() -> None:
    """``{"type":"message_delta",...}`` -> :class:`MessageDeltaEvent` wrapping the
    R201 :class:`MessageDeltaBody` + :class:`MessageDeltaUsage`."""
    ev = MessageStreamEvent.from_payload(
        {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 10}}
    )
    assert isinstance(ev, MessageDeltaEvent)
    assert isinstance(ev.delta, MessageDeltaBody)
    assert isinstance(ev.delta.stop_reason, EndTurn)
    assert isinstance(ev.usage, MessageDeltaUsage)
    assert ev.usage.output_tokens == 10


def test_message_stream_event_message_stop_fieldless() -> None:
    """``{"type":"message_stop"}`` -> the fieldless :class:`MessageStopEvent`
    (no per-instance ``__dict__`` under ``slots=True``)."""
    ev = MessageStreamEvent.from_payload({"type": "message_stop"})
    assert isinstance(ev, MessageStopEvent)
    assert not hasattr(ev, "__dict__")


def test_message_stream_event_content_block_start() -> None:
    """``{"type":"content_block_start",...}`` -> :class:`ContentBlockStartEvent`
    carrying the ``index`` + a R202 :class:`ContentBlock`."""
    ev = MessageStreamEvent.from_payload(
        {"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": "x"}}
    )
    assert isinstance(ev, ContentBlockStartEvent)
    assert ev.index == 2
    assert isinstance(ev.content_block, TextBlock)


def test_message_stream_event_content_block_delta() -> None:
    """``{"type":"content_block_delta",...}`` -> :class:`ContentBlockDeltaEvent`
    carrying the ``index`` + a R204 :class:`StreamDelta`."""
    ev = MessageStreamEvent.from_payload(
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hel"}}
    )
    assert isinstance(ev, ContentBlockDeltaEvent)
    assert ev.index == 0
    assert ev.delta.text == "Hel"


def test_message_stream_event_content_block_stop() -> None:
    """``{"type":"content_block_stop","index":N}`` -> :class:`ContentBlockStopEvent`."""
    ev = MessageStreamEvent.from_payload({"type": "content_block_stop", "index": 1})
    assert isinstance(ev, ContentBlockStopEvent)
    assert ev.index == 1


def test_message_stream_event_ping_fieldless() -> None:
    """``{"type":"ping"}`` -> the fieldless :class:`PingEvent` keepalive."""
    ev = MessageStreamEvent.from_payload({"type": "ping"})
    assert isinstance(ev, PingEvent)
    assert not hasattr(ev, "__dict__")


def test_message_stream_event_error() -> None:
    """``{"type":"error","error":{...}}`` -> :class:`StreamErrorEvent` wrapping the
    R201 :class:`StreamError` payload."""
    ev = MessageStreamEvent.from_payload(
        {"type": "error", "error": {"type": "overloaded_error", "message": "slow down"}}
    )
    assert isinstance(ev, StreamErrorEvent)
    assert isinstance(ev.error, StreamError)
    assert ev.error.message == "slow down"


def test_message_stream_event_unknown_type_raises() -> None:
    """grok tagged union has no catch-all -> an unknown ``type`` fails the parse
    (contrast the R201 StopReason catch-all, which must never fail a stream)."""
    with pytest.raises(ValueError, match="unknown message stream event type"):
        MessageStreamEvent.from_payload({"type": "future_event_kind"})


def test_message_stream_event_missing_type_raises() -> None:
    """A missing ``type`` tag is treated as unknown -> ``ValueError``."""
    with pytest.raises(ValueError, match="unknown message stream event type"):
        MessageStreamEvent.from_payload({"message": {}})


def test_message_stream_event_tolerant_nested_fallback() -> None:
    """A known ``type`` but missing/malformed nested payload tolerates (forward-
    compat, no crash): ``message_start`` without ``message`` -> empty
    :class:`MessagesResponse`; ``content_block_delta`` without ``delta`` -> the
    tagged-union tolerant fallback (a R204 :class:`TextDelta` with empty text)."""
    ev_start = MessageStreamEvent.from_payload({"type": "message_start"})
    assert isinstance(ev_start, MessageStartEvent)
    assert isinstance(ev_start.message, MessagesResponse)
    assert ev_start.message.id == ""
    ev_delta = MessageStreamEvent.from_payload({"type": "content_block_delta"})
    assert isinstance(ev_delta, ContentBlockDeltaEvent)
    assert ev_delta.delta.text == ""


# ---------------------------------------------------------------------------
# Value semantics: frozen + slots + hashable + union base.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obj",
    [
        Message.from_payload({}),
        MessagesResponse.from_payload({}),
        MessagesRequest.default(),
    ],
)
def test_envelope_containers_are_frozen(obj: object) -> None:
    """``@dataclass(frozen=True)`` -> mutating a real declared field raises
    (mirrors grok's immutable struct). Uses ``setattr`` with a variable field
    name (the first declared slot) so the raise is driven by the dataclass
    ``__setattr__`` rather than a slots-name lookup -- a frozen+slots dataclass
    raises ``FrozenInstanceError`` on a real field but a ``TypeError`` on an
    out-of-slots name (so the target must be a declared field)."""
    field_name = next(iter(type(obj).__slots__))
    with pytest.raises(FrozenInstanceError):
        setattr(obj, field_name, "rewritten")  # type: ignore[misc]


@pytest.mark.parametrize(
    "payload,expected_field",
    [
        ({"type": "message_start"}, "message"),
        ({"type": "message_delta"}, "delta"),
        ({"type": "content_block_start"}, "index"),
        ({"type": "content_block_delta"}, "index"),
        ({"type": "content_block_stop"}, "index"),
        ({"type": "error"}, "error"),
    ],
)
def test_stream_event_variants_are_frozen(
    payload: dict[str, object], expected_field: str
) -> None:
    """Each fielded :class:`MessageStreamEvent` variant is frozen -> mutating its
    declared field raises. The 2 fieldless variants (``message_stop`` / ``ping``)
    are covered by their own ``not hasattr(ev, "__dict__")`` slot-closure asserts
    (``slots=True`` + ``frozen=True`` close the attribute namespace; a fieldless
    variant has no real field to target, so it is verified via slot closure
    rather than a field-mutation raise)."""
    ev = MessageStreamEvent.from_payload(payload)
    with pytest.raises(FrozenInstanceError):
        setattr(ev, expected_field, "rewritten")  # type: ignore[misc]


def test_stream_event_variants_share_base() -> None:
    """All 8 stream-event variants are subclasses of the union base."""
    for kind in (
        "message_start",
        "message_delta",
        "message_stop",
        "content_block_start",
        "content_block_delta",
        "content_block_stop",
        "ping",
        "error",
    ):
        assert isinstance(MessageStreamEvent.from_payload({"type": kind}), MessageStreamEvent)


def test_tuple_carrying_envelopes_are_hashable() -> None:
    """The tuple-carrying containers store ``tuple`` (frozen -> hashable), so the
    whole envelope is hashable + equal-by-value (usable as dict keys, mirrors
    grok's immutable ``Vec``-carrying struct)."""
    resp_a = MessagesResponse.from_payload({"content": [{"type": "text", "text": "x"}]})
    resp_b = MessagesResponse.from_payload({"content": [{"type": "text", "text": "x"}]})
    assert resp_a == resp_b
    assert hash(resp_a) == hash(resp_b)

    req_a = MessagesRequest.from_payload({"messages": [{"role": "user", "content": "hi"}]})
    req_b = MessagesRequest.from_payload({"messages": [{"role": "user", "content": "hi"}]})
    assert req_a == req_b
    assert hash(req_a) == hash(req_b)
