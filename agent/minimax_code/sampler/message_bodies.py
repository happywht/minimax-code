"""Anthropic Messages API body-shaped leaf types (R204,
``xai-grok-sampling-types`` ``messages.rs``).

R204 lands the 4 "middle-layer" leaf types that sit between the R202
:class:`ContentBlock` union + the R203 request-side enums, and the still-deferred
mega-containers (``MessagesRequest`` + ``Message`` + ``MessagesResponse`` +
``MessageStreamEvent``). Each is a body shape the API carries -- a message's
``content``, the request's ``system`` param, a system block, or a streamed
content delta -- and all 4 close dependency-free (they consume only the R202
``CacheControl`` / ``ContentBlock`` already landed):

- :class:`SystemTextBlock` (leaf struct) -- the standalone ``messages.rs``
  ``TextBlock`` struct (``type`` + ``text`` + optional ``cache_control``), the
  element of a :class:`SystemParam` blocks list. Renamed from the grok
  ``TextBlock`` to avoid colliding with the R202 :class:`ContentBlock::Text`
  *variant* (also named ``TextBlock`` in :mod:`content_blocks`); the two are
  distinct wire shapes (the variant has no explicit ``type`` field, this struct
  does).
- :class:`SystemParam` (untagged 2-variant union) -- ``Text(String)`` vs
  ``Blocks(Vec<TextBlock>)``; the request's optional ``system`` field.
- :class:`MessageContent` (untagged 2-variant union) -- ``Text(String)`` vs
  ``Blocks(Vec<ContentBlock>)``; a message's ``content`` field.
- :class:`StreamDelta` (4-variant tagged union) -- the ``delta`` body of a
  ``content_block_delta`` stream event (text / partial-json / thinking /
  signature).

This module is no-I/O (``serde_json::Value`` -> ``dict`` / JSON value).
Migration map (grok -> Python):

- ``#[serde(tag="type", rename_all="snake_case")] enum`` (:class:`StreamDelta`)
  -> frozen+slots union base + subclasses; ``from_payload`` dispatches on the
  wire ``type`` tag. The union has NO catch-all, so an unknown tag raises
  ``ValueError`` (mirrors serde's strict tagged-union parse -- contrast the R201
  :class:`StopReason` catch-all, which must never fail a terminal stream).
- ``#[serde(untagged)] enum`` (:class:`SystemParam` / :class:`MessageContent`)
  -> frozen+slots union base + subclasses; ``from_payload`` matches on JSON
  shape (``str`` vs ``list``), mirroring serde's untagged try-each-variant order.
  A ``None``/absent value tolerates to the ``Text`` variant with an empty string
  (same forward-compat posture as the R202 :class:`ToolResultContent` untagged
  union); any other shape raises ``ValueError``.
- ``#[serde(rename="type")] r#type`` (:class:`SystemTextBlock`) -> ``type_``
  field (wire key ``"type"``); renamed to avoid shadowing the Python builtin.
- ``#[serde(skip_serializing_if="Option::is_none")]`` -> ``T | None = None``.

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip -- ``from_payload``
covers the parse direction the platform needs. The mega-containers
(``MessagesRequest`` + ``Message`` + ``MessagesResponse`` + the
``MessageStreamEvent`` wrapper) consume these leaves + the R202 union but are
separate layers landing in later rounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minimax_code.sampler.content_blocks import CacheControl, ContentBlock

# ---------------------------------------------------------------------------
# SystemTextBlock: standalone text-block struct (the SystemParam.Blocks element).
# ---------------------------------------------------------------------------
#
# This is the ``messages.rs`` ``pub struct TextBlock`` (NOT the ContentBlock::Text
# variant). Renamed ``SystemTextBlock`` to avoid colliding with the R202
# ContentBlock ``TextBlock`` variant in :mod:`content_blocks`. The two differ on
# the wire: the variant is ``{"type":"text","text":...,"cache_control":...}``
# parsed as a ContentBlock (no explicit ``type`` field on the dataclass -- the
# tag is consumed by the union dispatcher), while this struct carries the
# ``type`` field through (it is the element of a bare list, not a union member).


@dataclass(frozen=True, slots=True)
class SystemTextBlock:
    """A standalone text block (the element of a :class:`SystemParam` blocks
    list). The wire ``type`` is always ``"text"``; kept open as a ``str`` so a
    future shape never breaks the parse."""

    type_: str = "text"
    text: str = ""
    cache_control: CacheControl | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SystemTextBlock:
        """Tolerant constructor: ``type`` defaults to ``"text"`` and ``text`` to
        the empty string when absent; ``cache_control`` parses through
        :meth:`CacheControl.from_payload` only when a dict is on the wire."""
        raw_cc = payload.get("cache_control")
        return cls(
            type_=payload.get("type", "text"),
            text=payload.get("text", ""),
            cache_control=CacheControl.from_payload(raw_cc) if isinstance(raw_cc, dict) else None,
        )


# ---------------------------------------------------------------------------
# SystemParam: untagged 2-variant union (Text vs Blocks<TextBlock>).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SystemParam:
    """Request ``system`` param union base (untagged on the wire). Use
    :meth:`from_payload` for the JSON-value -> variant mapping."""

    @classmethod
    def from_payload(cls, payload: Any) -> SystemParam:
        """Match on JSON shape (mirrors serde's ``#[serde(untagged)]``). A JSON
        string (or ``None`` / absent) -> :class:`TextSystemParam`; a JSON array
        -> :class:`BlocksSystemParam` (parsing each item through
        :meth:`SystemTextBlock.from_payload`). Anything else raises
        ``ValueError``."""
        if payload is None or isinstance(payload, str):
            return TextSystemParam(text=payload or "")
        if isinstance(payload, list):
            return BlocksSystemParam(
                blocks=tuple(
                    SystemTextBlock.from_payload(item)
                    for item in payload
                    if isinstance(item, dict)
                )
            )
        raise ValueError(
            f"system param must be string or list, got {type(payload).__name__}"
        )


@dataclass(frozen=True, slots=True)
class TextSystemParam(SystemParam):
    """A plain-string system prompt (untagged ``String``)."""

    text: str


@dataclass(frozen=True, slots=True)
class BlocksSystemParam(SystemParam):
    """A list-of-blocks system prompt (untagged ``Vec<TextBlock>``). Each block
    is a :class:`SystemTextBlock` (the standalone text-block struct)."""

    blocks: tuple[SystemTextBlock, ...]


# ---------------------------------------------------------------------------
# MessageContent: untagged 2-variant union (Text vs Blocks<ContentBlock>).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MessageContent:
    """Message ``content`` union base (untagged on the wire). Use
    :meth:`from_payload` for the JSON-value -> variant mapping."""

    @classmethod
    def from_payload(cls, payload: Any) -> MessageContent:
        """Match on JSON shape (mirrors serde's ``#[serde(untagged)]``). A JSON
        string (or ``None`` / absent) -> :class:`TextMessageContent`; a JSON
        array -> :class:`BlocksMessageContent` (recursively parsing each item
        through :meth:`ContentBlock.from_payload`). Anything else raises
        ``ValueError``."""
        if payload is None or isinstance(payload, str):
            return TextMessageContent(text=payload or "")
        if isinstance(payload, list):
            return BlocksMessageContent(
                blocks=tuple(
                    ContentBlock.from_payload(item)
                    for item in payload
                    if isinstance(item, dict)
                )
            )
        raise ValueError(
            f"message content must be string or list, got {type(payload).__name__}"
        )


@dataclass(frozen=True, slots=True)
class TextMessageContent(MessageContent):
    """A plain-string message body (untagged ``String``)."""

    text: str


@dataclass(frozen=True, slots=True)
class BlocksMessageContent(MessageContent):
    """A list-of-blocks message body (untagged ``Vec<ContentBlock>``). Recursive
    on :class:`ContentBlock` -- a message may carry text / image / tool-use /
    tool-result / thinking blocks."""

    blocks: tuple[ContentBlock, ...]


# ---------------------------------------------------------------------------
# StreamDelta: 4-variant tagged union (tag="type", rename_all="snake_case").
# ---------------------------------------------------------------------------
#
# The ``delta`` body of a ``content_block_delta`` stream event. Internally
# tagged on the wire ``type`` field; serde tries each variant's struct shape,
# there is NO catch-all, so an unknown ``type`` fails the parse --
# ``from_payload`` mirrors that by raising ``ValueError`` (contrast the R201
# StopReason catch-all, which must never fail a stream).


@dataclass(frozen=True, slots=True)
class StreamDelta:
    """Stream content-delta union base (tagged on the wire ``type``). Use
    :meth:`from_payload` for the wire dict -> variant mapping."""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> StreamDelta:
        """Dispatch on the wire ``type`` tag. Known tags (``text_delta`` /
        ``input_json_delta`` / ``thinking_delta`` / ``signature_delta``) map to
        their variant; an unknown tag raises ``ValueError`` (mirrors serde's
        strict tagged-union parse -- no catch-all)."""
        kind = payload.get("type")
        if kind == "text_delta":
            return TextDelta(text=payload.get("text", ""))
        if kind == "input_json_delta":
            return InputJsonDelta(partial_json=payload.get("partial_json", ""))
        if kind == "thinking_delta":
            return ThinkingDelta(thinking=payload.get("thinking", ""))
        if kind == "signature_delta":
            return SignatureDelta(signature=payload.get("signature", ""))
        raise ValueError(f"unknown stream delta type: {kind!r}")


@dataclass(frozen=True, slots=True)
class TextDelta(StreamDelta):
    """``{"type":"text_delta","text":...}`` -- an incremental text chunk."""

    text: str


@dataclass(frozen=True, slots=True)
class InputJsonDelta(StreamDelta):
    """``{"type":"input_json_delta","partial_json":...}`` -- an incremental
    piece of a tool-use ``input`` JSON object (streamed argument assembly)."""

    partial_json: str


@dataclass(frozen=True, slots=True)
class ThinkingDelta(StreamDelta):
    """``{"type":"thinking_delta","thinking":...}`` -- an incremental extended
    thinking chunk. Distinct from the R202 :class:`ThinkingBlock` (a complete
    thinking block carrying its signature) -- this is the streamed delta."""

    thinking: str


@dataclass(frozen=True, slots=True)
class SignatureDelta(StreamDelta):
    """``{"type":"signature_delta","signature":...}`` -- the server-signed
    signature chunk that finalizes a thinking block."""

    signature: str


__all__ = [
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
]
