"""OpenAI ChatCompletion request-message body (R210, ``xai-grok-sampling-types`` ``types.rs``).

R210 lands the fifth slice of ``types.rs`` -- the assistant/user/tool message
body that a ``ChatCompletionRequest`` carries inside its ``messages`` list.
Closes against the R206 atomic slice (:class:`Role`) + the R207 middle-layer
slice (:class:`ChatMessageContent` + :class:`ToolCallRequest`) already landed;
zero external dependency (no ``crate::rs``, no ``serde_helpers``, no
``xai-grok-tools``).

- :class:`ChatRequestMessage` (struct) -- ``role`` (required) + ``content``
  (required) + ``name`` / ``tool_calls`` / ``tool_call_id`` / ``model_id`` /
  ``reasoning_content`` (all optional). Carries the 5 grok constructors
  (``system`` / ``user`` / ``assistant`` / ``assistant_tool_call`` / ``tool``),
  the ``is_system_message`` / ``text_content`` read-only helpers, and the
  ``with_text_content`` / ``with_appended_text`` copy-on-work mutators (renamed
  from grok's ``set_text_content`` / ``append_text_content`` because
  ``frozen=True`` forbids in-place mutation -- semantically equivalent to grok's
  ``&mut self``, same posture as the R207 :meth:`ToolCallRequest.with_id`).

This module is no-I/O (``serde_json::Value`` -> ``dict`` / wire value).
Migration map (grok -> Python):

- ``pub role: Role`` (required, no ``#[serde(default)]``, no
  ``skip_serializing_if``) -> a required ``role: Role`` field parsed strictly
  through :meth:`Role.from_payload` (a missing / non-string / unknown role
  raises ``ValueError`` -- mirrors serde's missing-required-field failure).
- ``pub content: MessageContent`` (required) -> a required ``content:
  ChatMessageContent`` field parsed through :meth:`ChatMessageContent.from_payload`
  (tolerates a missing / null wire value to the empty :class:`ChatTextContent`
  -- forward-compat, same posture as the R207 untagged union).
- ``#[serde(skip_serializing_if="Option::is_none")] Option<String>`` (``name`` /
  ``tool_call_id``) -> ``str | None = None`` (a ``dict.get`` with no runtime
  type check -- the wire value passes through verbatim).
- ``#[serde(default, skip_serializing_if="Vec::is_empty")] Vec<ToolCallRequest>``
  (``tool_calls``) -> ``tuple[ToolCallRequest, ...] = ()`` -- the ``default``
  attribute means a missing field deserializes to the empty Vec (NOT a
  failure); ``from_payload`` therefore tolerates a missing / null / non-list
  wire value to the empty tuple, and parses each dict item through
  :meth:`ToolCallRequest.from_payload` (non-dict items skipped). Same
  ``Vec<T> -> tuple`` posture as the R209 :class:`SearchSourceRss.links`, but
  over a complex element type.
- ``#[serde(default, skip_serializing_if="Option::is_none")] Option<String>``
  (``model_id`` / ``reasoning_content``) -> ``str | None = None`` (the
  ``default`` on an ``Option`` is ``None``; a missing field stays ``None``).
- non-dict payload -> ``ValueError`` (a dict is required to read the required
  ``role`` field -- mirrors serde's missing-required-field failure; contrast
  the R209 :class:`SearchParameters` all-Option struct, which tolerates a
  non-dict to the all-None instance).
- ``&mut self`` mutators (``set_text_content`` / ``append_text_content``) ->
  ``with_*`` copy-on-work return-new-instance methods (``frozen=True``
  forbids in-place mutation -- semantically equivalent to grok's
  ``&mut self -> ()`` when the caller rebinds, same posture as the R207
  :meth:`ToolCallRequest.with_id` builder).

Naming: :class:`ChatRequestMessage` is new (no Anthropic Messages API peer
collision -- the R205 :class:`Message` is the Anthropic single-turn envelope;
the ``ChatRequest`` prefix marks this as the OpenAI ChatCompletion peer).

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip -- ``from_payload``
covers the parse direction the platform needs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minimax_code.sampler.chat_completion_leaves import Role
from minimax_code.sampler.chat_completion_mid import (
    ChatBlocksContent,
    ChatMessageContent,
    ChatTextBlock,
    ChatTextContent,
    ToolCallRequest,
)


@dataclass(frozen=True, slots=True)
class ChatRequestMessage:
    """An OpenAI ChatCompletion request message (the ``messages[]`` body item).

    ``role`` is the required :class:`Role` (system / user / assistant / tool);
    ``content`` is the required :class:`ChatMessageContent` (a string or a list
    of :class:`ChatContentBlock`); ``name`` / ``tool_call_id`` / ``model_id`` /
    ``reasoning_content`` are optional string knobs; ``tool_calls`` is the
    optional tool-call list (each a :class:`ToolCallRequest`). Use the
    ``system`` / ``user`` / ``assistant`` / ``assistant_tool_call`` / ``tool``
    constructors for the canonical message shapes, or :meth:`from_payload` for
    the tolerant wire-dict -> instance mapping."""

    role: Role
    content: ChatMessageContent
    name: str | None = None
    tool_calls: tuple[ToolCallRequest, ...] = ()
    tool_call_id: str | None = None
    model_id: str | None = None
    reasoning_content: str | None = None

    @classmethod
    def from_payload(cls, payload: Any) -> ChatRequestMessage:
        """Tolerant constructor. ``role`` parses strictly through
        :meth:`Role.from_payload` (a missing / non-string / unknown role raises
        ``ValueError`` -- mirrors serde's missing-required-field failure);
        ``content`` parses through :meth:`ChatMessageContent.from_payload`
        (tolerates a missing / null wire value to the empty
        :class:`ChatTextContent`); ``tool_calls`` parses each dict item through
        :meth:`ToolCallRequest.from_payload` (non-dict items skipped, a missing
        / null / non-list wire value -> the empty tuple -- mirrors grok's
        ``#[serde(default)] Vec``); the three string knobs default to ``None``.
        A non-dict payload raises ``ValueError`` (a dict is required to read
        the required ``role`` field)."""
        if not isinstance(payload, dict):
            raise ValueError(
                f"chat request message must be a dict, got {type(payload).__name__}"
            )
        raw_tool_calls = payload.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            tool_calls: tuple[ToolCallRequest, ...] = tuple(
                ToolCallRequest.from_payload(item)
                for item in raw_tool_calls
                if isinstance(item, dict)
            )
        else:
            tool_calls = ()
        return cls(
            role=Role.from_payload(payload.get("role")),
            content=ChatMessageContent.from_payload(payload.get("content")),
            name=payload.get("name"),
            tool_calls=tool_calls,
            tool_call_id=payload.get("tool_call_id"),
            model_id=payload.get("model_id"),
            reasoning_content=payload.get("reasoning_content"),
        )

    # -- grok constructors (associated functions) --------------------------

    @classmethod
    def system(cls, content: str) -> ChatRequestMessage:
        """Mirror ``ChatRequestMessage::system(content)``: a system-role text
        message."""
        return cls(
            role=Role.SYSTEM,
            content=ChatTextContent(text=content),
        )

    @classmethod
    def user(cls, content: str) -> ChatRequestMessage:
        """Mirror ``ChatRequestMessage::user(content)``: a user-role text
        message."""
        return cls(
            role=Role.USER,
            content=ChatTextContent(text=content),
        )

    @classmethod
    def assistant(
        cls,
        content: str,
        model_id: str,
        reasoning_content: str | None = None,
    ) -> ChatRequestMessage:
        """Mirror ``ChatRequestMessage::assistant(content, model_id,
        reasoning_content: Option<String>)``: an assistant-role text message
        with the emitting ``model_id`` and an optional reasoning trace."""
        return cls(
            role=Role.ASSISTANT,
            content=ChatTextContent(text=content),
            model_id=model_id,
            reasoning_content=reasoning_content,
        )

    @classmethod
    def assistant_tool_call(cls, tool_call: ToolCallRequest) -> ChatRequestMessage:
        """Mirror ``ChatRequestMessage::assistant_tool_call(tool_call)``: an
        assistant-role message carrying a single tool call (empty text body)."""
        return cls(
            role=Role.ASSISTANT,
            content=ChatTextContent(text=""),
            tool_calls=(tool_call,),
        )

    @classmethod
    def tool(cls, tool_call_id: str, content: str) -> ChatRequestMessage:
        """Mirror ``ChatRequestMessage::tool(tool_call_id, content)``: a
        tool-role message echoing a tool result, keyed by ``tool_call_id``."""
        return cls(
            role=Role.TOOL,
            content=ChatTextContent(text=content),
            tool_call_id=tool_call_id,
        )

    # -- read-only helpers --------------------------------------------------

    def is_system_message(self) -> bool:
        """Mirror ``ChatRequestMessage::is_system_message``: ``role == System``."""
        return self.role == Role.SYSTEM

    def text_content(self) -> str:
        """Mirror ``ChatRequestMessage::text_content``: join the ``text`` field
        of every text block in ``content`` with ``\\n`` (image-url blocks
        contribute nothing, mirroring grok's ``filter_map`` over the ``Text``
        variant)."""
        return "\n".join(
            block.text
            for block in self.content.to_blocks()
            if isinstance(block, ChatTextBlock)
        )

    # -- copy-on-work mutators (frozen-safe) -------------------------------

    def with_text_content(self, text: str) -> ChatRequestMessage:
        """Mirror ``ChatRequestMessage::set_text_content(&mut self, text)``,
        renamed to ``with_*``: return a copy with ``content`` replaced by a
        ``ChatTextContent(text)`` (``frozen=True`` forbids in-place mutation --
        copy-on-work is semantically equivalent to grok's ``&mut self`` when
        the caller rebinds, same posture as the R207
        :meth:`ToolCallRequest.with_id` builder)."""
        return ChatRequestMessage(
            role=self.role,
            content=ChatTextContent(text=text),
            name=self.name,
            tool_calls=self.tool_calls,
            tool_call_id=self.tool_call_id,
            model_id=self.model_id,
            reasoning_content=self.reasoning_content,
        )

    def with_appended_text(self, text: str) -> ChatRequestMessage:
        """Mirror ``ChatRequestMessage::append_text_content(&mut self, text)``,
        renamed to ``with_*``: return a copy with ``text`` appended. An empty
        ``content`` delegates to :meth:`with_text_content` (a replace); a
        :class:`ChatTextContent` is string-concatenated; a
        :class:`ChatBlocksContent` gets a new :class:`ChatTextBlock` appended.
        Copy-on-work (``frozen=True`` forbids in-place mutation)."""
        if self.content.is_empty():
            return self.with_text_content(text)
        if isinstance(self.content, ChatTextContent):
            return ChatRequestMessage(
                role=self.role,
                content=ChatTextContent(text=self.content.text + text),
                name=self.name,
                tool_calls=self.tool_calls,
                tool_call_id=self.tool_call_id,
                model_id=self.model_id,
                reasoning_content=self.reasoning_content,
            )
        if isinstance(self.content, ChatBlocksContent):
            return ChatRequestMessage(
                role=self.role,
                content=ChatBlocksContent(
                    blocks=(*self.content.blocks, ChatTextBlock(text=text))
                ),
                name=self.name,
                tool_calls=self.tool_calls,
                tool_call_id=self.tool_call_id,
                model_id=self.model_id,
                reasoning_content=self.reasoning_content,
            )
        return self.with_text_content(text)


__all__ = ["ChatRequestMessage"]
