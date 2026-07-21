"""Conversation-layer content part (R221, ``xai-grok-sampling-types``
``conversation.rs``).

R221 lands :class:`ContentPart` (``conversation.rs`` ~354) -- the
conversation-layer content-part tagged union: one element of a user/assistant
message body (plain text or an image reference). This is the sampler package's
second **internally-tagged** (``tag = "type"``) mixed enum -- the fourth serde
representation shape to land in this module family, after R84's ``untagged``
:class:`JsonRpcId`, R89's ``internally-tagged`` :class:`HookEvent`, and R220's
``externally-tagged`` :class:`ConversationToolChoice`. ``#[serde(tag = "type",
rename_all = "snake_case")]`` means each struct variant serializes at the JSON
edge as a single object carrying the snake_case variant name under the
``"type"`` discriminator key plus the variant's own field:

- ``Text { text }`` -> ``{"type": "text", "text": <text>}``
- ``Image { url }`` -> ``{"type": "image", "url": <url>}``

This is the standard OpenAI content-part wire shape (the per-element body of a
ChatCompletions request's ``content`` array), hoisted into the conversation
layer. Distinct from the wire-layer :class:`ContentBlock` family (R202,
messages.rs) -- that family is the Anthropic Messages API block surface; this
one is the conversation-internal content-part peer. No barrel collision (the
``ContentPart`` / ``TextPart`` / ``ImagePart`` names are free at the package
surface -- the wire layer exposes ``ContentBlock`` / ``TextBlock`` /
``ImageBlock``, and the ``part`` vs ``block`` suffix keeps the two families
distinct), so -- unlike the R219 ``ConversationStopReason`` / R220
``ConversationToolChoice`` renames -- no ``Conversation`` prefix is needed; the
``<Type>Part`` variant naming mirrors the R202 ``<Type>Block`` precedent.

Dependency closure: zero external. Each variant carries a single ``Arc<str>``
(Python ``str``); no sampler types referenced. Unblocks the conversation
consumer layer (``UserItem`` / ``AssistantItem`` carry ``Vec<ContentPart>``
bodies) for a later slice -- the strategic reason this leaf lands before the
other zero-dependency conversation.rs leaves (``ToolCall`` / ``ToolSpec`` /
``HostedTool`` / ``SystemItem`` sit on different axes).

This module is no-I/O (pure value-level). Migration map (grok -> Python):

- ``enum ContentPart { Text { text: Arc<str> }, Image { url: Arc<str> } }`` ->
  :class:`ContentPart` frozen+slots union base carrying the hand-written
  internally-tagged (de)serializer pair :meth:`from_payload` /
  :meth:`as_payload` (mirrors ``#[derive(Serialize, Deserialize)]`` +
  ``#[serde(tag = "type", rename_all = "snake_case")]``).
- ``Text { text: Arc<str> }`` -> :class:`TextPart` (``text: str``).
- ``Image { url: Arc<str> }`` -> :class:`ImagePart` (``url: str``).

YAGNI: none -- both variants plus both serde directions land together (the
union crosses the wire as the per-element body shape; an in-program-only union
would defer serde, but this one is wire-shaped).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ContentPart:
    """One element of a message body (internally-tagged tagged union).

    The conversation-layer content part -- API-agnostic, hoisted from the
    OpenAI content-part wire shape. Two struct variants: :class:`TextPart`
    (plain text) and :class:`ImagePart` (a URL or base64 data-URI image).
    Distinct from the wire-layer :class:`ContentBlock` family (types.rs /
    messages.rs Anthropic surface); build one via :meth:`from_payload` and
    inspect one via :meth:`as_payload`.
    """

    @classmethod
    def from_payload(cls, raw: Any) -> ContentPart:
        """Internally-tagged wire parser (mirrors serde Deserialize).

        A dict carrying a string ``"type"`` discriminator -> the matching
        variant: ``"text"`` + a string ``text`` field -> :class:`TextPart`;
        ``"image"`` + a string ``url`` field -> :class:`ImagePart`; anything
        else (non-dict, missing/non-string tag, missing/non-string field,
        unknown tag) -> ``ValueError``. The enum has no ``#[serde(other)]``
        catch-all, so an unknown discriminator fails -- strict parity with the
        R220 :class:`ConversationToolChoice` (contrast the R218 ``UNKNOWN``
        catch-all enums which never raise). Extra keys outside ``type`` + the
        variant field are tolerated (serde's default ignores unknown fields on
        a struct variant)."""
        if not isinstance(raw, dict):
            raise ValueError(
                "content part must be a dict, " f"got {type(raw).__name__}"
            )
        tag = raw.get("type")
        if not isinstance(tag, str):
            raise ValueError(
                "content part 'type' must be a string, "
                f"got {type(tag).__name__}"
            )
        if tag == "text":
            text = raw.get("text")
            if not isinstance(text, str):
                raise ValueError(
                    "content part 'text' must be a string, "
                    f"got {type(text).__name__}"
                )
            return TextPart(text=text)
        if tag == "image":
            url = raw.get("url")
            if not isinstance(url, str):
                raise ValueError(
                    "content part 'url' must be a string, "
                    f"got {type(url).__name__}"
                )
            return ImagePart(url=url)
        raise ValueError(f"unknown content part type tag: {tag!r}")

    def as_payload(self) -> dict[str, Any]:
        """Internally-tagged wire serializer (mirrors serde Serialize).

        :class:`TextPart` -> ``{"type": "text", "text": <text>}``;
        :class:`ImagePart` -> ``{"type": "image", "url": <url>}``."""
        if isinstance(self, TextPart):
            return {"type": "text", "text": self.text}
        if isinstance(self, ImagePart):
            return {"type": "image", "url": self.url}
        raise TypeError(f"unknown ContentPart variant: {self!r}")


@dataclass(frozen=True, slots=True)
class TextPart(ContentPart):
    """Plain text content (wire ``{"type": "text", "text": ...}``).

    ``text`` is the literal text string (grok ``Arc<str>`` -> Python ``str``)."""

    text: str


@dataclass(frozen=True, slots=True)
class ImagePart(ContentPart):
    """Image content (wire ``{"type": "image", "url": ...}``).

    ``url`` is the image URL or base64 data URI (grok ``Arc<str>`` -> Python
    ``str``)."""

    url: str


__all__ = [
    "ContentPart",
    "ImagePart",
    "TextPart",
]
