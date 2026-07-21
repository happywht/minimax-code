"""OpenAI ChatCompletion middle-layer leaf types (R207,
``xai-grok-sampling-types`` ``types.rs``).

R207 lands the second slice of ``types.rs`` -- the middle-layer leaves of the
OpenAI-compatible ChatCompletion type family that compose the R206 atomic
leaves into request/response body shapes. Each closes dependency-free against
the R206 slice (they consume only ``ImageUrl`` / ``ToolType`` /
``ToolChoiceFunction`` / ``ToolCallFunction`` / ``PromptTokensDetails`` /
``CompletionTokensDetails`` already landed):

- :class:`ChatContentBlock` (2-variant tagged union, ``tag="type"``) -- the
  ``text`` vs ``image_url`` block carried inside a
  :class:`ChatMessageContent` blocks list (the OpenAI peer of the R202
  :class:`ContentBlock` union). Consumes :class:`ImageUrl`.
- :class:`ChatMessageContent` (untagged 2-variant union) -- ``Text(String)``
  vs ``Blocks(Vec<ChatContentBlock>)``; a ChatCompletion message's ``content``
  field. Renamed from the grok ``MessageContent`` to avoid colliding with the
  R204 :class:`MessageContent` (the Anthropic Messages API peer, whose Blocks
  list carries the R202 :class:`ContentBlock` instead). Carries the
  :meth:`is_empty` / :meth:`to_blocks` helpers. Consumes :class:`ChatContentBlock`.
- :class:`ToolChoice` (untagged 2-variant union) -- ``Preset(String)`` vs
  ``Function { kind, function }``; the ``tool_choice`` request knob. Carries
  the ``auto`` / ``none`` / ``required`` / ``function`` constructors. Consumes
  :class:`ToolType` + :class:`ToolChoiceFunction`.
- :class:`ToolCallRequest` (struct) -- ``id`` / ``kind`` / ``function``; an
  assistant message's emitted tool call. Carries the ``with_function`` /
  ``with_id`` constructors (``with_function`` mirrors grok's
  ``ToolCallRequest::function`` associated function, renamed to dodge the
  Python field/method namespace collision -- Rust keeps the field and the
  associated function in separate namespaces, Python does not). Consumes
  :class:`ToolType` + :class:`ToolCallFunction`.
- :class:`ChatUsage` (struct) -- the token-usage counter on a
  :class:`ChatCompletionResponse`. Renamed from the grok ``Usage`` to avoid
  colliding with the R201 :class:`MessagesUsage` (the Anthropic Messages API
  peer). Consumes :class:`PromptTokensDetails` + :class:`CompletionTokensDetails`.

The still-deferred heavier containers (``ChatCompletionRequest`` /
``ChatCompletionResponse`` / ``ChatCompletionChunk`` + their inner
``ChatRequestMessage`` / ``ChatChoice`` / ``ChatResponseMessage`` /
``ChatChunkChoice`` / ``ChatChunkDelta`` / ``ToolCallDelta`` /
``ToolCallFunctionDelta`` / ``SearchParameters`` / ``SearchSource`` shapes) +
the ``TraceContext`` trait + the compaction enums (``CompactionAtTokens`` /
``CompactionsRemaining``) + the duplicate ``ReasoningEffort`` (with its
``to_responses_api`` / ``from_responses_api`` ``crate::rs`` coupling) + the
``ApiBackend`` / ``SamplingConfig`` cluster land in later rounds -- they depend
on ``crate::rs`` / ``crate::serde_helpers`` / the ``xai-grok-tools`` re-exports
(``ToolDefinition`` / ``FunctionTool``) / each other, so they are NOT
zero-dependency leaves.

Dependency closure: zero external (no ``crate::rs``, no ``xai-grok-tools``, no
``serde_helpers``). All 11 symbols close against the R206 slice.

This module is no-I/O (``serde_json::Value`` -> ``dict`` / wire value).
Migration map (grok -> Python):

- ``#[serde(tag="type", rename="...")] enum`` (:class:`ChatContentBlock`) ->
  frozen+slots union base + subclasses; ``from_payload`` dispatches on the wire
  ``type`` tag. The union has NO untagged catch-all, so an unknown tag raises
  ``ValueError`` (mirrors serde's strict tagged-union parse -- contrast the R201
  :class:`StopReason` catch-all, which must never fail a terminal stream).
- ``#[serde(untagged)] enum`` (:class:`ChatMessageContent` /
  :class:`ToolChoice`) -> frozen+slots union base + subclasses;
  ``from_payload`` matches on JSON shape (``str`` vs ``list`` for
  :class:`ChatMessageContent`; ``str`` vs ``dict`` for :class:`ToolChoice`),
  mirroring serde's untagged try-each-variant order. A ``None`` / absent
  :class:`ChatMessageContent` tolerates to the ``Text`` variant with an empty
  string (same forward-compat posture as the R204 :class:`MessageContent`
  untagged union); any other shape raises ``ValueError``.
- ``#[serde(rename="type")] r#type`` / ``kind`` -> ``kind`` field holding a
  :class:`ToolType` (parsed from the wire ``type`` key).
- plain ``#[derive(Serialize, Deserialize)] struct`` (:class:`ToolCallRequest`
  / :class:`ChatUsage`) -> ``@dataclass(frozen=True, slots=True)`` with a
  tolerant ``from_payload``.
- ``#[serde(skip_serializing_if="Option::is_none")]`` -> ``T | None = None``.
- builder methods (``ToolCallRequest::with_id``) -> return a new frozen instance
  (``frozen=True`` forbids in-place mutation; the builder is therefore
  copy-on-write, semantically equivalent to grok's ``mut self -> Self``).

Naming: the 5 union families carry a ``Chat`` prefix to avoid colliding with
the Anthropic Messages API peers in the package barrel --
:class:`ChatContentBlock` vs the R202 :class:`ContentBlock`,
:class:`ChatMessageContent` vs the R204 :class:`MessageContent`,
:class:`ChatUsage` vs the R201 :class:`MessagesUsage`. :class:`ToolChoice` /
:class:`ToolCallRequest` are new (distinct from the R203 :class:`ToolChoiceParam`
and the R206 :class:`ToolCallFunction`).

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip -- ``from_payload``
covers the parse direction the platform needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from minimax_code.sampler.chat_completion_leaves import (
    CompletionTokensDetails,
    ImageUrl,
    PromptTokensDetails,
    ToolCallFunction,
    ToolChoiceFunction,
    ToolType,
)

# ---------------------------------------------------------------------------
# ChatContentBlock: 2-variant tagged union (tag="type", rename "text"/"image_url").
# ---------------------------------------------------------------------------
#
# The OpenAI ChatCompletion content block. Internally tagged on the wire
# ``type`` field; serde tries each variant's struct shape, there is NO
# catch-all, so an unknown ``type`` fails the parse -- ``from_payload`` mirrors
# that by raising ``ValueError`` (contrast the R201 StopReason catch-all). The
# ``Chat`` prefix dodges the R202 ``ContentBlock`` (the Anthropic 5-variant
# peer); the two never collide in the package barrel.


@dataclass(frozen=True, slots=True)
class ChatContentBlock:
    """OpenAI ChatCompletion content-block union base (tagged on the wire
    ``type``). A :class:`ChatMessageContent` blocks list is a list of these.
    Use :meth:`from_payload` for the wire dict -> variant mapping."""

    @classmethod
    def from_payload(cls, payload: Any) -> ChatContentBlock:
        """Dispatch on the wire ``type`` tag. Known tags (``text`` /
        ``image_url``) map to their variant; an unknown tag / non-dict raises
        ``ValueError`` (mirrors serde's strict tagged-union parse -- no
        catch-all)."""
        if not isinstance(payload, dict):
            raise ValueError(
                f"chat content block must be a dict, got {type(payload).__name__}"
            )
        kind = payload.get("type")
        if kind == "text":
            return ChatTextBlock(text=payload.get("text", ""))
        if kind == "image_url":
            return ChatImageUrlBlock(image_url=ImageUrl.from_payload(payload.get("image_url")))
        raise ValueError(f"unknown chat content block type: {kind!r}")


@dataclass(frozen=True, slots=True)
class ChatTextBlock(ChatContentBlock):
    """``{"type":"text","text":...}`` -- the ``ChatContentBlock::Text`` variant
    (inline text field). Distinct from the R202 :class:`TextBlock` (the
    Anthropic ``ContentBlock::Text`` variant) -- hence the ``Chat`` prefix."""

    text: str


@dataclass(frozen=True, slots=True)
class ChatImageUrlBlock(ChatContentBlock):
    """``{"type":"image_url","image_url":{...}}`` -- the
    ``ChatContentBlock::ImageUrl`` variant. Distinct from the R202
    :class:`ImageBlock` (the Anthropic peer, which carries an
    :class:`ImageSource` instead of an :class:`ImageUrl`)."""

    image_url: ImageUrl


# ---------------------------------------------------------------------------
# ChatMessageContent: untagged 2-variant union (Text vs Blocks<ChatContentBlock>).
# ---------------------------------------------------------------------------
#
# Renamed from the grok ``MessageContent`` to dodge the R204 ``MessageContent``
# (the Anthropic Messages API peer, whose Blocks list carries the R202
# ContentBlock). The two differ on the wire only in the block type carried by
# the Blocks variant; the untagged str-vs-list shape match is identical.


@dataclass(frozen=True, slots=True)
class ChatMessageContent:
    """ChatCompletion message ``content`` union base (untagged on the wire).
    Use :meth:`from_payload` for the JSON-value -> variant mapping."""

    @classmethod
    def from_payload(cls, payload: Any) -> ChatMessageContent:
        """Match on JSON shape (mirrors serde's ``#[serde(untagged)]``). A JSON
        string (or ``None`` / absent) -> :class:`ChatTextContent`; a JSON array
        -> :class:`ChatBlocksContent` (recursively parsing each item through
        :meth:`ChatContentBlock.from_payload`). Anything else raises
        ``ValueError``."""
        if payload is None or isinstance(payload, str):
            return ChatTextContent(text=payload or "")
        if isinstance(payload, list):
            return ChatBlocksContent(
                blocks=tuple(
                    ChatContentBlock.from_payload(item)
                    for item in payload
                    if isinstance(item, dict)
                )
            )
        raise ValueError(
            f"chat message content must be string or list, got {type(payload).__name__}"
        )

    def is_empty(self) -> bool:
        """Mirror ``MessageContent::is_empty``: ``Blocks`` is empty when its
        list is empty, ``Text`` when its string is empty."""
        if isinstance(self, ChatBlocksContent):
            return len(self.blocks) == 0
        if isinstance(self, ChatTextContent):
            return len(self.text) == 0
        return True

    def to_blocks(self) -> tuple[ChatContentBlock, ...]:
        """Mirror ``MessageContent::blocks``: a ``Blocks`` value yields its
        list as-is; a ``Text`` value is wrapped into a single
        :class:`ChatTextBlock` (mirrors grok's
        ``vec![ChatContentBlock::Text { text }]``).

        Renamed from grok's ``blocks`` to ``to_blocks``: Python's field/method
        namespaces collide (the :class:`ChatBlocksContent` subclass declares a
        ``blocks`` slot); Rust keeps the method in a separate namespace from the
        ``Blocks(Vec<...>)`` enum-variant payload, Python does not -- a ``blocks``
        method on the union base would be shadowed by the subclass field's slot
        descriptor, so ``content.blocks`` resolves to the tuple and
        ``content.blocks()`` raises ``TypeError: 'tuple' object is not
        callable``. The ``to_`` prefix is the Python-idiomatic conversion-method
        spelling (cf. ``dict.keys`` accessor vs ``.to_blocks`` converter)."""
        if isinstance(self, ChatBlocksContent):
            return self.blocks
        if isinstance(self, ChatTextContent):
            return (ChatTextBlock(text=self.text),)
        return ()


@dataclass(frozen=True, slots=True)
class ChatTextContent(ChatMessageContent):
    """A plain-string ChatCompletion message body (untagged ``String``)."""

    text: str


@dataclass(frozen=True, slots=True)
class ChatBlocksContent(ChatMessageContent):
    """A list-of-blocks ChatCompletion message body (untagged
    ``Vec<ChatContentBlock>``). Each block is a :class:`ChatContentBlock`
    (text / image_url)."""

    blocks: tuple[ChatContentBlock, ...]


# ---------------------------------------------------------------------------
# ToolChoice: untagged 2-variant union (Preset(String) vs Function{kind,function}).
# ---------------------------------------------------------------------------
#
# The ``tool_choice`` request knob. Untagged: serde tries ``Preset(String)``
# first (a JSON string matches), then ``Function { kind, function }`` (a JSON
# object matches). ``from_payload`` mirrors that str-vs-dict shape match.


@dataclass(frozen=True, slots=True)
class ToolChoice:
    """``tool_choice`` union base (untagged on the wire). Use
    :meth:`from_payload` for the JSON-value -> variant mapping, or the
    ``auto`` / ``none`` / ``required`` / ``function`` constructors for the
    canonical presets."""

    @classmethod
    def from_payload(cls, payload: Any) -> ToolChoice:
        """Match on JSON shape (mirrors serde's ``#[serde(untagged)]``). A JSON
        string -> :class:`PresetToolChoice` (``"auto"`` / ``"none"`` /
        ``"required"``); a JSON object -> :class:`FunctionToolChoice`
        (``{"type":"function","function":{"name":...}}``). Anything else raises
        ``ValueError``."""
        if isinstance(payload, str):
            return PresetToolChoice(value=payload)
        if isinstance(payload, dict):
            return FunctionToolChoice(
                kind=ToolType.from_payload(payload.get("type", "function")),
                function=ToolChoiceFunction.from_payload(payload.get("function")),
            )
        raise ValueError(
            f"tool choice must be string or dict, got {type(payload).__name__}"
        )

    @classmethod
    def auto(cls) -> ToolChoice:
        """Mirror ``ToolChoice::auto``: the ``"auto"`` preset."""
        return PresetToolChoice(value="auto")

    @classmethod
    def none(cls) -> ToolChoice:
        """Mirror ``ToolChoice::none``: the ``"none"`` preset (``none`` is a
        method name here -- ``None`` is the Python keyword, the lower-case
        wire string is not)."""
        return PresetToolChoice(value="none")

    @classmethod
    def required(cls) -> ToolChoice:
        """Mirror ``ToolChoice::required``: the ``"required"`` preset."""
        return PresetToolChoice(value="required")

    @classmethod
    def function(cls, name: str) -> ToolChoice:
        """Mirror ``ToolChoice::function(name)``: a named-function choice
        (``{"type":"function","function":{"name":...}}``)."""
        return FunctionToolChoice(
            kind=ToolType.FUNCTION,
            function=ToolChoiceFunction(name=name),
        )


@dataclass(frozen=True, slots=True)
class PresetToolChoice(ToolChoice):
    """A preset tool-choice string (untagged ``String``): ``"auto"`` /
    ``"none"`` / ``"required"`` (or any future preset the API ships)."""

    value: str


@dataclass(frozen=True, slots=True)
class FunctionToolChoice(ToolChoice):
    """A named-function tool choice (untagged ``Function { kind, function }``).
    ``kind`` is the wire ``type`` (always ``"function"`` today); ``function``
    names the tool to call."""

    kind: ToolType
    function: ToolChoiceFunction


# ---------------------------------------------------------------------------
# ToolCallRequest: an assistant message's emitted tool call (struct).
# ---------------------------------------------------------------------------
#
# ``id`` / ``kind`` (wire ``type``) / ``function``. The ``function`` /
# ``with_id`` constructors mirror grok's ``ToolCallRequest::function`` +
# ``with_id`` builder. ``with_id`` returns a new frozen instance (``frozen=True``
# forbids in-place mutation; copy-on-write is semantically equivalent to grok's
# ``mut self -> Self``).


@dataclass(frozen=True, slots=True)
class ToolCallRequest:
    """An assistant message's emitted tool call.

    ``id`` is optional (absent when the platform omits it); ``kind`` is the wire
    ``type`` discriminator (always :attr:`ToolType.FUNCTION` today); ``function``
    carries the call's name + serialized arguments. Use :meth:`with_function` to
    build a no-id call, :meth:`with_id` to attach an id (copy-on-write)."""

    function: ToolCallFunction = field(default_factory=ToolCallFunction)
    id: str | None = None
    kind: ToolType = ToolType.FUNCTION

    @classmethod
    def from_payload(cls, payload: Any) -> ToolCallRequest:
        """Tolerant constructor: ``id`` defaults to ``None``, ``kind`` to
        :attr:`ToolType.FUNCTION`, ``function`` to an empty
        :class:`ToolCallFunction` when the payload is missing / null / not a
        dict / lacks the key."""
        if not isinstance(payload, dict):
            return cls()
        raw_func = payload.get("function")
        return cls(
            function=(
                ToolCallFunction.from_payload(raw_func)
                if isinstance(raw_func, dict)
                else ToolCallFunction()
            ),
            id=payload.get("id"),
            kind=ToolType.from_payload(payload.get("type", "function")),
        )

    @classmethod
    def with_function(cls, name: str, arguments: str) -> ToolCallRequest:
        """Mirror ``ToolCallRequest::function(name, arguments)``: a no-id call
        with ``kind = Function`` and a fresh :class:`ToolCallFunction`.

        Renamed from grok's ``function`` to ``with_function``: Python's
        field/method namespaces collide (the struct has a ``function`` field);
        Rust keeps the associated function in a separate namespace, Python does
        not -- the classmethod would shadow the dataclass field descriptor and
        break the ``__init__`` parameter binding."""
        return cls(
            function=ToolCallFunction(name=name, arguments=arguments),
            id=None,
            kind=ToolType.FUNCTION,
        )

    def with_id(self, id: str) -> ToolCallRequest:
        """Mirror ``ToolCallRequest::with_id(self, id)``: return a copy with the
        id set. ``frozen=True`` forbids in-place mutation, so the builder is
        copy-on-write (semantically equivalent to grok's ``mut self -> Self``)."""
        return ToolCallRequest(function=self.function, id=id, kind=self.kind)


# ---------------------------------------------------------------------------
# ChatUsage: ChatCompletion token-usage counter (struct, renamed from Usage).
# ---------------------------------------------------------------------------
#
# Renamed from the grok ``Usage`` to dodge the R201 ``MessagesUsage`` (the
# Anthropic Messages API peer). ``prompt_tokens_details`` /
# ``completion_tokens_details`` are optional nested breakdowns;
# ``cost_in_usd_ticks`` is the xAI billing extension (1 USD = 1e10 ticks).


@dataclass(frozen=True, slots=True)
class ChatUsage:
    """OpenAI ChatCompletion token-usage counter.

    ``prompt_tokens`` / ``completion_tokens`` / ``total_tokens`` are the bare
    counters; ``prompt_tokens_details`` / ``completion_tokens_details`` are the
    optional nested breakdowns; ``cost_in_usd_ticks`` is the xAI billing
    extension (1 USD = 1e10 ticks; ``None`` when unreported). Distinct from the
    R201 :class:`MessagesUsage` (the Anthropic Messages API peer) -- hence the
    ``Chat`` prefix."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_tokens_details: PromptTokensDetails | None = None
    completion_tokens_details: CompletionTokensDetails | None = None
    cost_in_usd_ticks: int | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> ChatUsage:
        """Tolerant constructor: each counter defaults to 0 and each breakdown
        to ``None`` when the payload is missing / null / not a dict / lacks the
        key. A dict-valued breakdown parses through its ``from_payload``;
        anything else stays ``None`` (mirrors grok's ``Option::None``)."""
        if not isinstance(payload, dict):
            return cls()
        raw_ptd = payload.get("prompt_tokens_details")
        raw_ctd = payload.get("completion_tokens_details")
        return cls(
            prompt_tokens=payload.get("prompt_tokens", 0),
            completion_tokens=payload.get("completion_tokens", 0),
            total_tokens=payload.get("total_tokens", 0),
            prompt_tokens_details=(
                PromptTokensDetails.from_payload(raw_ptd)
                if isinstance(raw_ptd, dict)
                else None
            ),
            completion_tokens_details=(
                CompletionTokensDetails.from_payload(raw_ctd)
                if isinstance(raw_ctd, dict)
                else None
            ),
            cost_in_usd_ticks=payload.get("cost_in_usd_ticks"),
        )


__all__ = [
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
]
