"""Anthropic Messages API content-block union + its 3 direct dependencies (R202,
``xai-grok-sampling-types`` ``messages.rs``).

``ContentBlock`` is the discriminated union used in BOTH request and response
message bodies -- a message's ``content`` is a list of these, and a tool-result
block's ``content`` is either a string or a nested list of them. R202 lands the
union plus its 3 direct dependencies:

- :class:`CacheControl` (leaf) -- the prompt-cache annotation.
- :class:`ImageSource` (2-variant tagged union) -- base64 vs url.
- :class:`ToolResultContent` (untagged 2-variant union) -- recursive on
  :class:`ContentBlock` (a tool result may itself be a list of blocks).

This module is no-I/O (``serde_json::Value`` -> ``dict``). Migration map
(grok -> Python):

- ``#[serde(tag="type", rename_all="snake_case")] enum`` -> frozen+slots base
  + subclasses; ``from_payload`` dispatches on the wire ``type`` tag. The union
  has NO untagged catch-all, so an unknown tag raises ``ValueError`` (mirrors
  serde's strict tagged-union parse -- contrast the R201 :class:`StopReason`
  catch-all, which must never fail a terminal stream).
- ``#[serde(untagged)] enum`` -> frozen+slots base + subclasses;
  ``from_payload`` matches on JSON shape (``str`` vs ``list``).
- ``#[serde(rename="type")] r#type`` -> ``type_`` field (wire key ``"type"``);
  renamed to avoid shadowing the Python builtin.
- ``serde_json::Value`` -> ``Any`` (arbitrary JSON, e.g. a tool-use ``input``
  arguments object).
- ``#[serde(skip_serializing_if="Option::is_none")]`` -> ``T | None = None``.

Naming: the 5 ``ContentBlock`` subclasses carry a ``Block`` suffix to avoid
collisions in the package barrel -- :class:`ToolUseBlock` (the ContentBlock
variant) is distinct from :class:`minimax_code.sampler.messages.ToolUse` (the
``StopReason`` variant landed in R201). :class:`TextBlock` here is the
``ContentBlock::Text`` *variant* (inline fields), NOT the standalone
``SystemParam`` ``TextBlock`` struct (request-side, deferred).

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip -- ``from_payload``
covers the parse direction the platform needs. The request-side containers
(``MessagesRequest`` + ``Message`` + ``MessageContent`` + ``SystemParam`` + the
standalone ``TextBlock`` struct + ``MessageRole`` + ``ToolParam`` +
``ToolChoiceParam`` + ``ThinkingConfig`` + ``ThinkingDisplay`` + ``OutputConfig``
+ ``OutputFormat`` + ``Metadata``) and the response/streaming-side containers
(``MessagesResponse`` + ``MessageStreamEvent`` wrapper + ``StreamDelta``) consume
this union but are separate layers landing in later rounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# CacheControl: prompt-cache annotation leaf.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CacheControl:
    """Prompt-cache annotation on a content block.

    The wire ``type`` is ``"ephemeral"`` (the only cache tier Anthropic exposes
    today); the field is kept open as a ``str`` so a future tier never breaks
    the parse.
    """

    type_: str = "ephemeral"

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> CacheControl:
        """Tolerant constructor: a missing ``type`` keeps the documented
        ``"ephemeral"`` default."""
        return cls(type_=payload.get("type", "ephemeral"))


# ---------------------------------------------------------------------------
# ImageSource: 2-variant tagged union (tag="type", rename_all="snake_case").
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImageSource:
    """Image source union base (tagged on the wire ``type``). Use
    :meth:`from_payload` for the wire dict -> variant mapping."""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ImageSource:
        """Dispatch on the wire ``type`` tag. Known tags (``base64`` / ``url``)
        map to their variant; an unknown tag raises ``ValueError`` (mirrors
        serde's strict tagged-union parse -- this union has no catch-all)."""
        kind = payload.get("type")
        if kind == "base64":
            return Base64ImageSource(
                media_type=payload.get("media_type", ""),
                data=payload.get("data", ""),
            )
        if kind == "url":
            return UrlImageSource(url=payload.get("url", ""))
        raise ValueError(f"unknown image source type: {kind!r}")


@dataclass(frozen=True, slots=True)
class Base64ImageSource(ImageSource):
    """``{"type":"base64","media_type":...,"data":...}`` -- inline base64."""

    media_type: str
    data: str


@dataclass(frozen=True, slots=True)
class UrlImageSource(ImageSource):
    """``{"type":"url","url":...}`` -- image referenced by URL."""

    url: str


# ---------------------------------------------------------------------------
# ToolResultContent: untagged 2-variant union (recursive on ContentBlock).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolResultContent:
    """Tool-result content union base (untagged on the wire). Use
    :meth:`from_payload` for the JSON-value -> variant mapping."""

    @classmethod
    def from_payload(cls, payload: Any) -> ToolResultContent:
        """Match on JSON shape (mirrors serde's ``#[serde(untagged)]``). A JSON
        string (or ``None`` / absent) -> :class:`TextToolResultContent`; a JSON
        array -> :class:`BlocksToolResultContent` (recursively parsing each item
        through :meth:`ContentBlock.from_payload`). Anything else raises
        ``ValueError``."""
        if payload is None or isinstance(payload, str):
            return TextToolResultContent(text=payload or "")
        if isinstance(payload, list):
            return BlocksToolResultContent(
                blocks=tuple(
                    ContentBlock.from_payload(item)
                    for item in payload
                    if isinstance(item, dict)
                )
            )
        raise ValueError(
            f"tool result content must be string or list, got {type(payload).__name__}"
        )


@dataclass(frozen=True, slots=True)
class TextToolResultContent(ToolResultContent):
    """A plain-string tool result (untagged ``String``)."""

    text: str


@dataclass(frozen=True, slots=True)
class BlocksToolResultContent(ToolResultContent):
    """A list-of-blocks tool result (untagged ``Vec<ContentBlock>``). Recursive
    on :class:`ContentBlock` -- a tool result may itself carry content blocks
    (e.g. an image returned to the model)."""

    blocks: tuple[ContentBlock, ...]


# ---------------------------------------------------------------------------
# ContentBlock: 5-variant tagged union (tag="type", rename_all="snake_case").
# ---------------------------------------------------------------------------
#
# The union is internally tagged on the wire ``type`` field. serde tries each
# variant's struct shape; there is NO untagged catch-all here, so an unknown
# ``type`` fails the parse -- from_payload mirrors that by raising ValueError
# (contrast the R201 StopReason catch-all, which must never fail a stream).


@dataclass(frozen=True, slots=True)
class ContentBlock:
    """Content-block union base (tagged on the wire ``type``). A message's
    ``content`` is a list of these (request + response + tool-result). Use
    :meth:`from_payload` for the wire dict -> variant mapping."""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ContentBlock:
        """Parse a wire content-block dict. Known ``type`` tags map to their
        variant; an unknown tag raises ``ValueError`` (mirrors serde's strict
        tagged-union parse -- no catch-all). Required nested unions
        (``image.source``) raise when malformed; scalar fields default to empty
        strings / ``None`` when absent (forwards-compat with future shapes)."""
        kind = payload.get("type")
        if kind == "text":
            raw_cc = payload.get("cache_control")
            return TextBlock(
                text=payload.get("text", ""),
                cache_control=(
                    CacheControl.from_payload(raw_cc) if isinstance(raw_cc, dict) else None
                ),
            )
        if kind == "image":
            raw_src = payload.get("source")
            if not isinstance(raw_src, dict):
                raise ValueError("image content block requires a 'source' object")
            return ImageBlock(source=ImageSource.from_payload(raw_src))
        if kind == "tool_use":
            return ToolUseBlock(
                id=payload.get("id", ""),
                name=payload.get("name", ""),
                input=payload.get("input"),
            )
        if kind == "tool_result":
            raw_cc = payload.get("cache_control")
            return ToolResultBlock(
                tool_use_id=payload.get("tool_use_id", ""),
                content=ToolResultContent.from_payload(payload.get("content")),
                cache_control=(
                    CacheControl.from_payload(raw_cc) if isinstance(raw_cc, dict) else None
                ),
            )
        if kind == "thinking":
            return ThinkingBlock(
                thinking=payload.get("thinking", ""),
                signature=payload.get("signature", ""),
            )
        raise ValueError(f"unknown content block type: {kind!r}")


@dataclass(frozen=True, slots=True)
class TextBlock(ContentBlock):
    """``{"type":"text","text":...,"cache_control":...?}`` -- the
    ``ContentBlock::Text`` variant (inline fields, NOT the standalone
    ``SystemParam`` ``TextBlock`` struct which is request-side and deferred)."""

    text: str
    cache_control: CacheControl | None = None


@dataclass(frozen=True, slots=True)
class ImageBlock(ContentBlock):
    """``{"type":"image","source":{...}}`` -- an image content block."""

    source: ImageSource


@dataclass(frozen=True, slots=True)
class ToolUseBlock(ContentBlock):
    """``{"type":"tool_use","id":...,"name":...,"input":{...}}`` -- the model
    requesting a tool call. ``input`` is arbitrary JSON (the tool's arguments
    object, ``serde_json::Value``). Distinct from the R201 ``StopReason::ToolUse``
    variant -- hence the ``Block`` suffix."""

    id: str
    name: str
    input: Any = None


@dataclass(frozen=True, slots=True)
class ToolResultBlock(ContentBlock):
    """``{"type":"tool_result","tool_use_id":...,"content":...,
    "cache_control":...?}`` -- a tool result returned to the model. ``content``
    is either a plain string or a list of content blocks (see
    :class:`ToolResultContent`)."""

    tool_use_id: str
    content: ToolResultContent
    cache_control: CacheControl | None = None


@dataclass(frozen=True, slots=True)
class ThinkingBlock(ContentBlock):
    """``{"type":"thinking","thinking":...,"signature":...}`` -- an extended
    thinking block carrying the text + the server-signed signature."""

    thinking: str
    signature: str


__all__ = [
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
]
