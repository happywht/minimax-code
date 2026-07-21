"""Anthropic Messages API mega-container types (R205,
``xai-grok-sampling-types`` ``messages.rs``).

R205 lands the 4 outermost wire containers of ``messages.rs`` -- the layers
that aggregate every leaf landed in R201-R204 into the full request /
response / streaming shapes a client sends + receives. This round closes the
``messages.rs`` wire-type layer: every ``pub`` type in the file now has a
Python home.

- :class:`Message` -- a single conversation turn (``role`` + ``content``),
  the element of a :class:`MessagesRequest` ``messages`` list.
- :class:`MessagesResponse` -- the non-streaming ``POST /v1/messages`` reply
  (``id`` / ``type`` / ``role`` / ``content`` / ``model`` / ``stop_reason``
  / ``usage``).
- :class:`MessagesRequest` -- the ``POST /v1/messages`` request body
  (``model`` + ``messages`` + ``max_tokens`` + 11 optional knobs), the
  ``#[derive(Default)]`` container.
- :class:`MessageStreamEvent` -- the 8-variant SSE event wrapper tagged on
  the wire ``type`` (``message_start`` / ``message_delta`` / ``message_stop``
  / ``content_block_start`` / ``content_block_delta`` / ``content_block_stop``
  / ``ping`` / ``error``).

Dependency closure (all R201-R204 leaves): :class:`Message` consumes the R203
:class:`MessageRole` + the R204 :class:`MessageContent`; :class:`MessagesResponse`
consumes the R202 :class:`ContentBlock` + the R201 :class:`StopReason` /
:class:`MessagesUsage`; :class:`MessagesRequest` consumes :class:`Message` +
the R203 :class:`ThinkingConfig` / :class:`ToolChoiceParam` / :class:`ToolParam`
/ :class:`OutputConfig` / :class:`Metadata` + the R204 :class:`SystemParam`;
:class:`MessageStreamEvent` consumes :class:`MessagesResponse` + the R201
:class:`MessageDeltaBody` / :class:`MessageDeltaUsage` / :class:`StreamError`
+ the R202 :class:`ContentBlock` + the R204 :class:`StreamDelta`.

This module is no-I/O (``serde_json::Value`` -> ``dict``). Migration map
(grok -> Python):

- plain ``#[derive(Serialize, Deserialize)] struct`` -> ``@dataclass(frozen=True,
  slots=True)`` with a tolerant ``from_payload`` classmethod.
- ``Vec<T>`` -> ``tuple[...]`` (frozen -> hashable).
- ``Option<T>`` + ``#[serde(skip_serializing_if)]`` -> ``T | None = None``; the
  ``from_payload`` parses the nested type only when the wire value is present
  (a missing key / ``null`` stays ``None``, mirroring serde's ``Option``).
- ``#[derive(Default)]`` (:class:`MessagesRequest`) -> a ``default()``
  classmethod yielding the all-defaults instance (``model=""`` /
  ``messages=()`` / ``max_tokens=0``, every ``Option`` ``None``).
- ``#[serde(tag="type", rename_all="snake_case")] enum``
  (:class:`MessageStreamEvent`) -> frozen+slots union base + 8 subclasses;
  ``from_payload`` dispatches on the wire ``type`` tag. The union has NO
  catch-all, so an unknown tag raises ``ValueError`` (mirrors serde's strict
  tagged-union parse -- contrast the R201 :class:`StopReason` catch-all which
  must never fail a terminal stream). Fieldless variants (``message_stop`` /
  ``ping``) map to fieldless subclasses (mirrors the R201 ``EndTurn`` unit
  shape).
- ``#[serde(rename="type")] r#type`` (:class:`MessagesResponse`) -> ``type_``
  field (wire key ``"type"``); renamed to avoid shadowing the Python builtin.

Naming: the 8 :class:`MessageStreamEvent` subclasses carry an ``Event`` suffix
so they do not collide with the wrapped inner types or the package barrel --
:class:`MessageStartEvent` (wraps :class:`MessagesResponse`) is distinct from
:class:`MessagesResponse` itself; :class:`StreamErrorEvent` (wraps the R201
:class:`StreamError` payload) is distinct from that payload.

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip -- ``from_payload``
covers the parse direction the platform needs. The two fieldless event
variants (:class:`MessageStopEvent` / :class:`PingEvent`) carry no payload.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from minimax_code.sampler.content_blocks import ContentBlock
from minimax_code.sampler.message_bodies import (
    MessageContent,
    StreamDelta,
    SystemParam,
)
from minimax_code.sampler.messages import (
    MessageDeltaBody,
    MessageDeltaUsage,
    MessagesUsage,
    StopReason,
    StreamError,
    parse_stop_reason,
)
from minimax_code.sampler.request_params import (
    MessageRole,
    Metadata,
    OutputConfig,
    ThinkingConfig,
    ToolChoiceParam,
    ToolParam,
)

# ---------------------------------------------------------------------------
# Message: a single conversation turn (role + content).
# ---------------------------------------------------------------------------
#
# The element of a MessagesRequest ``messages`` list. ``role`` is the R203
# MessageRole (lowercase wire enum); ``content`` is the R204 MessageContent
# untagged union (string vs list of R202 ContentBlock).


@dataclass(frozen=True, slots=True)
class Message:
    """A single conversation turn (the element of ``messages``).

    ``role`` defaults to :attr:`MessageRole.USER` when the wire value is not a
    known role string; ``content`` defaults to an empty-text variant when
    absent. Use :meth:`from_payload` for the wire dict mapping."""

    role: MessageRole = MessageRole.USER
    content: MessageContent = field(default_factory=lambda: MessageContent.from_payload(""))

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Message:
        """Tolerant constructor: ``role`` parses through :class:`MessageRole`
        when the wire value is a known role string (falling back to ``user``
        when absent / non-string / an unknown role -- forward-compat so a future
        role never fails the parse); ``content`` parses through
        :meth:`MessageContent.from_payload` (which tolerates ``None`` / absent
        to an empty-text variant)."""
        raw_role = payload.get("role")
        try:
            role = MessageRole(raw_role) if isinstance(raw_role, str) else MessageRole.USER
        except ValueError:
            role = MessageRole.USER
        return cls(role=role, content=MessageContent.from_payload(payload.get("content")))


# ---------------------------------------------------------------------------
# MessagesResponse: the non-streaming POST /v1/messages reply.
# ---------------------------------------------------------------------------
#
# Plain struct (no Default derive). ``content`` is Vec<ContentBlock> -> tuple;
# ``stop_reason`` is the optional R201 StopReason (parses through the catch-all
# so a future stop reason never fails a parse); ``usage`` is the R201
# MessagesUsage (tolerant defaults when the server omits it). The wire ``type``
# field is renamed to ``type_`` to avoid shadowing the Python builtin.


@dataclass(frozen=True, slots=True)
class MessagesResponse:
    """Non-streaming ``POST /v1/messages`` reply.

    ``id`` / ``model`` default to the empty string; ``type_`` defaults to
    ``"message"`` and ``role`` to ``"assistant"`` (per the grok wire comments);
    ``content`` is a tuple of R202 :class:`ContentBlock`; ``stop_reason`` is the
    optional R201 :class:`StopReason`; ``usage`` is the R201
    :class:`MessagesUsage`."""

    id: str = ""
    type_: str = "message"
    role: str = "assistant"
    content: tuple[ContentBlock, ...] = ()
    model: str = ""
    stop_reason: StopReason | None = None
    usage: MessagesUsage = field(
        default_factory=lambda: MessagesUsage(input_tokens=0, output_tokens=0)
    )

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> MessagesResponse:
        """Tolerant constructor: ``content`` recursively parses each list item
        through :meth:`ContentBlock.from_payload` (skipping non-dict items);
        ``stop_reason`` parses through the catch-all :func:`parse_stop_reason`
        only when a string is on the wire; ``usage`` parses through
        :meth:`MessagesUsage.from_payload` when a dict is present (else the
        all-zero default)."""
        raw_reason = payload.get("stop_reason")
        raw_usage = payload.get("usage")
        return cls(
            id=payload.get("id", ""),
            type_=payload.get("type", "message"),
            role=payload.get("role", "assistant"),
            content=tuple(
                ContentBlock.from_payload(item)
                for item in payload.get("content", [])
                if isinstance(item, dict)
            ),
            model=payload.get("model", ""),
            stop_reason=parse_stop_reason(raw_reason) if isinstance(raw_reason, str) else None,
            usage=MessagesUsage.from_payload(raw_usage if isinstance(raw_usage, dict) else {}),
        )


# ---------------------------------------------------------------------------
# MessagesRequest: the POST /v1/messages request body (#[derive(Default)]).
# ---------------------------------------------------------------------------
#
# The 11 optional knobs are all Option<T> with skip_serializing_if; from_payload
# parses each nested type only when the wire value is present, so a missing key
# / null stays None (mirrors serde's Option). The required fields (model /
# messages / max_tokens) default for direct construction ("" / () / 0),
# mirroring the #[derive(Default)] impl.


@dataclass(frozen=True, slots=True)
class MessagesRequest:
    """``POST /v1/messages`` request body (``#[derive(Default)]``).

    ``model`` / ``messages`` / ``max_tokens`` default to ``""`` / ``()`` / ``0``
    for direct construction; the 11 optional knobs (``system`` / ``tools`` /
    ``tool_choice`` / ``temperature`` / ``top_p`` / ``top_k`` / ``stream`` /
    ``stop_sequences`` / ``thinking`` / ``output_config`` / ``metadata``) are
    ``None`` unless present on the wire. Use :meth:`from_payload` for the wire
    dict mapping, or :meth:`default` for the all-defaults instance."""

    model: str = ""
    messages: tuple[Message, ...] = ()
    max_tokens: int = 0
    system: SystemParam | None = None
    tools: tuple[ToolParam, ...] | None = None
    tool_choice: ToolChoiceParam | None = None
    temperature: float | None = None
    top_p: float | None = None
    top_k: int | None = None
    stream: bool | None = None
    stop_sequences: tuple[str, ...] | None = None
    thinking: ThinkingConfig | None = None
    output_config: OutputConfig | None = None
    metadata: Metadata | None = None

    @classmethod
    def default(cls) -> MessagesRequest:
        """Mirror ``#[derive(Default)]``: ``model=""`` / ``messages=()`` /
        ``max_tokens=0`` and every ``Option`` ``None``."""
        return cls()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> MessagesRequest:
        """Tolerant constructor: ``messages`` recursively parses each list item
        through :meth:`Message.from_payload`; each optional nested knob parses
        through its R203/R204 leaf ``from_payload`` only when a value is on the
        wire (``system`` / ``tool_choice`` / ``thinking`` / ``output_config`` /
        ``metadata`` parse when dict-shaped; ``system`` parses for any non-null
        value since it is an untagged union; ``tools`` / ``stop_sequences``
        become tuples when list-shaped; the scalar knobs pass through
        verbatim)."""
        raw_system = payload.get("system")
        raw_tools = payload.get("tools")
        raw_choice = payload.get("tool_choice")
        raw_thinking = payload.get("thinking")
        raw_output = payload.get("output_config")
        raw_meta = payload.get("metadata")
        raw_stops = payload.get("stop_sequences")
        return cls(
            model=payload.get("model", ""),
            messages=tuple(
                Message.from_payload(item)
                for item in payload.get("messages", [])
                if isinstance(item, dict)
            ),
            max_tokens=payload.get("max_tokens", 0),
            system=SystemParam.from_payload(raw_system) if raw_system is not None else None,
            tools=(
                tuple(ToolParam.from_payload(t) for t in raw_tools if isinstance(t, dict))
                if isinstance(raw_tools, list)
                else None
            ),
            tool_choice=ToolChoiceParam.from_payload(raw_choice) if isinstance(raw_choice, dict) else None,
            temperature=payload.get("temperature"),
            top_p=payload.get("top_p"),
            top_k=payload.get("top_k"),
            stream=payload.get("stream"),
            stop_sequences=tuple(raw_stops) if isinstance(raw_stops, list) else None,
            thinking=ThinkingConfig.from_payload(raw_thinking) if isinstance(raw_thinking, dict) else None,
            output_config=OutputConfig.from_payload(raw_output) if isinstance(raw_output, dict) else None,
            metadata=Metadata.from_payload(raw_meta) if isinstance(raw_meta, dict) else None,
        )


# ---------------------------------------------------------------------------
# MessageStreamEvent: 8-variant tagged union (tag="type", rename_all="snake_case").
# ---------------------------------------------------------------------------
#
# The top-level SSE event. Internally tagged on the wire ``type`` field; serde
# tries each variant's struct shape, there is NO catch-all, so an unknown
# ``type`` fails the parse -- from_payload mirrors that by raising ValueError
# (contrast the R201 StopReason catch-all, which must never fail a stream). The
# 8 wire tags are the snake_case of the variant names: message_start /
# message_delta / message_stop / content_block_start / content_block_delta /
# content_block_stop / ping / error.


@dataclass(frozen=True, slots=True)
class MessageStreamEvent:
    """Top-level SSE streaming event union base (tagged on the wire ``type``).
    Use :meth:`from_payload` for the wire dict -> variant mapping. No catch-all
    -- an unknown ``type`` raises ``ValueError`` (strict tagged-union parse)."""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> MessageStreamEvent:
        """Dispatch on the wire ``type`` tag. Known tags (``message_start`` /
        ``message_delta`` / ``message_stop`` / ``content_block_start`` /
        ``content_block_delta`` / ``content_block_stop`` / ``ping`` / ``error``)
        map to their variant; an unknown tag raises ``ValueError`` (mirrors
        serde's strict tagged-union parse -- no catch-all). Nested payloads
        parse through their leaf ``from_payload`` when dict-shaped, else fall
        back to a tolerant empty instance."""
        kind = payload.get("type")
        if kind == "message_start":
            raw_msg = payload.get("message")
            return MessageStartEvent(
                message=MessagesResponse.from_payload(raw_msg if isinstance(raw_msg, dict) else {})
            )
        if kind == "message_delta":
            raw_delta = payload.get("delta")
            raw_usage = payload.get("usage")
            return MessageDeltaEvent(
                delta=MessageDeltaBody.from_payload(raw_delta if isinstance(raw_delta, dict) else {}),
                usage=MessageDeltaUsage.from_payload(raw_usage if isinstance(raw_usage, dict) else {}),
            )
        if kind == "message_stop":
            return MessageStopEvent()
        if kind == "content_block_start":
            raw_cb = payload.get("content_block")
            return ContentBlockStartEvent(
                index=payload.get("index", 0),
                content_block=ContentBlock.from_payload(
                    raw_cb if isinstance(raw_cb, dict) else {"type": "text", "text": ""}
                ),
            )
        if kind == "content_block_delta":
            raw_delta = payload.get("delta")
            return ContentBlockDeltaEvent(
                index=payload.get("index", 0),
                delta=StreamDelta.from_payload(
                    raw_delta if isinstance(raw_delta, dict) else {"type": "text_delta", "text": ""}
                ),
            )
        if kind == "content_block_stop":
            return ContentBlockStopEvent(index=payload.get("index", 0))
        if kind == "ping":
            return PingEvent()
        if kind == "error":
            raw_err = payload.get("error")
            return StreamErrorEvent(
                error=StreamError.from_payload(raw_err if isinstance(raw_err, dict) else {})
            )
        raise ValueError(f"unknown message stream event type: {kind!r}")


@dataclass(frozen=True, slots=True)
class MessageStartEvent(MessageStreamEvent):
    """``{"type":"message_start","message":{...}}`` -- opens the stream with the
    initial :class:`MessagesResponse` (id / model / role / empty content)."""

    message: MessagesResponse


@dataclass(frozen=True, slots=True)
class MessageDeltaEvent(MessageStreamEvent):
    """``{"type":"message_delta","delta":{...},"usage":{...}}`` -- the terminal
    stop-reason + final token usage."""

    delta: MessageDeltaBody
    usage: MessageDeltaUsage


@dataclass(frozen=True, slots=True)
class MessageStopEvent(MessageStreamEvent):
    """``{"type":"message_stop"}`` -- the stream is complete. Fieldless unit
    variant."""


@dataclass(frozen=True, slots=True)
class ContentBlockStartEvent(MessageStreamEvent):
    """``{"type":"content_block_start","index":N,"content_block":{...}}`` -- a
    new content block begins (text / image / tool-use / thinking)."""

    index: int
    content_block: ContentBlock


@dataclass(frozen=True, slots=True)
class ContentBlockDeltaEvent(MessageStreamEvent):
    """``{"type":"content_block_delta","index":N,"delta":{...}}`` -- an
    incremental chunk for the block at ``index`` (a R204 :class:`StreamDelta`)."""

    index: int
    delta: StreamDelta


@dataclass(frozen=True, slots=True)
class ContentBlockStopEvent(MessageStreamEvent):
    """``{"type":"content_block_stop","index":N}`` -- the block at ``index`` is
    complete."""

    index: int


@dataclass(frozen=True, slots=True)
class PingEvent(MessageStreamEvent):
    """``{"type":"ping"}`` -- a keepalive. Fieldless unit variant."""


@dataclass(frozen=True, slots=True)
class StreamErrorEvent(MessageStreamEvent):
    """``{"type":"error","error":{...}}`` -- an mid-stream error frame (a R201
    :class:`StreamError` payload)."""

    error: StreamError


__all__ = [
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
]
