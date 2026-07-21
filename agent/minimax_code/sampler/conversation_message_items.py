"""Conversation-layer message items (R224, ``xai-grok-sampling-types``
``conversation.rs``).

R224 lands the four conversation-layer **message item** structs
(``conversation.rs`` ~62 / ~189 / ~233 / ~260) -- :class:`SystemItem` /
:class:`UserItem` / :class:`AssistantItem` / :class:`ToolResultItem`, the
"Message Items" block. A message item is one row of a conversation turn: a
system prompt, a user turn, an assistant turn (text + tool calls), or a tool
result the client feeds back. These four are the struct members of the (still
un-migrated) :class:`ConversationItem` tagged union -- R224 lands them as a
cohesive leaf cluster so the union consumer is unblocked for a later slice.

This is the second **plain struct** slice in ``conversation.rs`` (after the
R222 :class:`ToolCall` / :class:`ToolSpec` tool-definition pair), and the
first to compose **multiple already-migrated sampler leaves** into one
struct: :class:`UserItem.content` / :class:`ToolResultItem.images` are
``Vec<ContentPart>`` (R221 :class:`ContentPart`), :class:`AssistantItem.
tool_calls`` is ``Vec<ToolCall>`` (R222 :class:`ToolCall`),
:class:`UserItem.synthetic_reason`` / :class:`prior_turn_interrupt` are the
R218 catch-all enums (:class:`SyntheticReason` / :class:`PriorTurnInterrupt`),
:class:`AssistantItem.reasoning_effort`` is the R206 strict enum
(:class:`ReasoningEffort`), and :class:`AssistantItem.model_fingerprint`` uses
the R211 :func:`empty_string_as_none` hook (with the ``system_fingerprint``
serde alias). The whole dependency closure is now landed -- the strategic
reason this block was deferred until R224.

Strict-required vs tolerant (the R222 discipline, applied per field): a field
with NO ``#[serde(default)]`` is **required** -- a missing key or wrong-typed
value raises ``ValueError`` (mirrors serde's failure on a default-less struct
field, the same strict-no-catch-all philosophy as the R221
:class:`ContentPart` / R220 :class:`ConversationToolChoice` enums); a field
WITH ``#[serde(default)]`` is **tolerant** -- absent / null collapses to the
default. One deliberate deviation: :class:`UserItem` carries
``#[derive(Default)]`` and its ``content`` field has NO field-level
``#[serde(default)]`` (so grok serde fails on a missing ``content``), but the
``#[derive(Default)]`` programmatic surface makes an empty ``content`` a legal
state (``UserItem::default()`` -> ``content: vec![]``). R224 honors the
programmatic surface: ``content`` defaults to ``()`` on the dataclass and
``from_payload`` tolerates a missing / null ``content`` -> ``()`` (rather than
grok's serde failure). This is recorded below + in the field docstring; the
other three structs have no ``#[derive(Default)]`` and stay strictly required
on their default-less fields.

Dependency closure: zero external (no ``crate::rs``). Every field type is a
landed sampler leaf. :class:`AssistantItem.model_fingerprint`` carries the
``alias = "system_fingerprint"`` + ``deserialize_with =
"empty_string_as_none"`` serde pair (the same alias the R211 docstring
documents) -- ``from_payload`` checks ``model_fingerprint`` first, falls back
to the ``system_fingerprint`` alias, then runs :func:`empty_string_as_none`
(``""`` -> ``None``).

No barrel collision: :class:`SystemItem` / :class:`UserItem` /
:class:`AssistantItem` / :class:`ToolResultItem` are free at the package
surface (no wire-layer peer -- the R203 :class:`MessageRole` is a 4-variant
lowercase enum, not a struct; the R206 :class:`Role` is the ChatCompletion
peer). Like the R222 :class:`ToolCall`, no ``Conversation`` prefix is needed;
the bare names mirror grok's own module-local ``SystemItem`` / ``UserItem`` /
``AssistantItem`` / ``ToolResultItem``.

This module is no-I/O (pure value-level). Migration map (grok -> Python):

- ``struct SystemItem { content: Arc<str> }`` -> :class:`SystemItem`
  (``content: str``). Required (no ``#[serde(default)]``).
- ``#[derive(Default)] struct UserItem { content: Vec<ContentPart>,
  synthetic_reason: Option<SyntheticReason>, prior_turn_interrupt:
  Option<PriorTurnInterrupt>, prompt_index: Option<usize> }`` ->
  :class:`UserItem`. ``Vec<ContentPart>`` -> ``tuple[ContentPart, ...]``;
  ``Option<T>`` -> ``T | None = None``. ``content`` defaults to ``()`` (the
  ``#[derive(Default)]`` programmatic surface) and ``from_payload`` tolerates
  a missing / null ``content`` -> ``()`` (deliberate deviation -- see above).
- ``struct AssistantItem { content: Arc<str>, tool_calls: Vec<ToolCall>,
  model_id: Option<String>, model_fingerprint: Option<String>, reasoning_effort:
  Option<ReasoningEffort> }`` -> :class:`AssistantItem`. ``content`` required;
  ``Vec<ToolCall>`` -> ``tuple[ToolCall, ...] = ()``; ``Option<T>`` ->
  ``T | None = None``; ``model_fingerprint`` resolves the ``system_fingerprint``
  alias + :func:`empty_string_as_none`; ``reasoning_effort`` uses the strict
  :meth:`ReasoningEffort.from_payload` (unknown effort -> ``ValueError``).
- ``struct ToolResultItem { tool_call_id: String, content: Arc<str>, images:
  Vec<ContentPart> }`` -> :class:`ToolResultItem`. ``tool_call_id`` + ``content``
  required; ``Vec<ContentPart>`` -> ``tuple[ContentPart, ...] = ()``.
- ``#[derive(Serialize, Deserialize)]`` (all four, no ``#[serde(tag)]``) ->
  hand-written :meth:`from_payload` / :meth:`as_payload` pair (the R222 plain-
  struct discipline).

YAGNI: the :class:`ConversationItem` tagged-union consumer (it wraps these
four as its struct variants + carries the ``#[serde(tag = "role", ...)]``
discriminator -- lands in a later round); the ``ReasoningContent`` /
``BackendToolCallItem`` peers (blocked on ``crate::rs`` -- the Responses API
native types not yet migrated).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minimax_code.sampler.chat_completion_leaves import ReasoningEffort
from minimax_code.sampler.conversation_content_part import ContentPart
from minimax_code.sampler.conversation_enums import PriorTurnInterrupt, SyntheticReason
from minimax_code.sampler.conversation_tool_defs import ToolCall
from minimax_code.sampler.serde_helpers import empty_string_as_none


@dataclass(frozen=True, slots=True)
class SystemItem:
    """A system-prompt row of a conversation turn.

    ``content`` is the system prompt body (grok ``Arc<str>`` -> Python ``str``).
    Required (no ``#[serde(default)]``) -- a missing key or non-string value on
    the wire raises ``ValueError`` (mirrors serde's failure on a default-less
    required struct field). One of the four struct members of the (un-migrated)
    :class:`ConversationItem` tagged union."""

    content: str

    @classmethod
    def from_payload(cls, raw: Any) -> SystemItem:
        """Strict wire parser (mirrors serde Deserialize on a default-less struct).

        A dict carrying a string ``content`` -> the struct; anything else
        (non-dict, missing key, non-string value) -> ``ValueError``. Extra keys
        are tolerated (serde's default ignores unknown struct fields)."""
        if not isinstance(raw, dict):
            raise ValueError(f"system item must be a dict, got {type(raw).__name__}")
        content = raw.get("content")
        if not isinstance(content, str):
            raise ValueError(
                "system item 'content' must be a string, " f"got {type(content).__name__}"
            )
        return cls(content=content)

    def as_payload(self) -> dict[str, Any]:
        """Wire serializer (mirrors serde Serialize).

        -> ``{"content": <content>}``."""
        return {"content": self.content}


@dataclass(frozen=True, slots=True)
class UserItem:
    """A user-turn row of a conversation turn.

    ``content`` is the user message body -- a sequence of content parts (grok
    ``Vec<ContentPart>`` -> ``tuple[ContentPart, ...]``); ``synthetic_reason``
    / ``prior_turn_interrupt`` are the R218 catch-all enums (a synthesized
    prompt marker / a prior-turn interrupt reason); ``prompt_index`` is an
    optional positional index. ``#[derive(Default)]`` -- the dataclass defaults
    mirror it (``content = ()``, the three options ``= None``), and
    :meth:`from_payload` tolerates a missing / null ``content`` -> ``()``
    (deliberate deviation from grok's serde-strict ``content``-required: the
    ``#[derive(Default)]`` programmatic surface makes an empty ``content`` a
    legal state). One of the four struct members of the (un-migrated)
    :class:`ConversationItem` tagged union."""

    content: tuple[ContentPart, ...] = ()
    synthetic_reason: SyntheticReason | None = None
    prior_turn_interrupt: PriorTurnInterrupt | None = None
    prompt_index: int | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> UserItem:
        """Tolerant-content + tolerant-optional wire parser.

        ``content`` (a list of content-part dicts) -> a tuple of
        :class:`ContentPart`; a missing / null ``content`` -> ``()`` (the
        ``#[derive(Default)]`` surface); a non-list ``content`` ->
        ``ValueError``. ``synthetic_reason`` / ``prior_turn_interrupt`` default
        to ``None`` when absent / null and parse via the catch-all
        :meth:`SyntheticReason.from_payload` / :meth:`PriorTurnInterrupt.
        from_payload` (never raise). ``prompt_index`` defaults to ``None`` when
        absent / null and must be an int when present. Extra keys are
        tolerated."""
        if not isinstance(raw, dict):
            raise ValueError(f"user item must be a dict, got {type(raw).__name__}")
        content = _parse_content_part_list(raw.get("content"), "user item 'content'")
        synthetic_reason = _parse_optional_enum(raw.get("synthetic_reason"), SyntheticReason)
        prior_turn_interrupt = _parse_optional_enum(
            raw.get("prior_turn_interrupt"), PriorTurnInterrupt
        )
        prompt_index = _parse_optional_int(raw.get("prompt_index"), "user item 'prompt_index'")
        return cls(
            content=content,
            synthetic_reason=synthetic_reason,
            prior_turn_interrupt=prior_turn_interrupt,
            prompt_index=prompt_index,
        )

    def as_payload(self) -> dict[str, Any]:
        """Wire serializer (mirrors serde Serialize).

        -> ``{"content": [<part>, ...]}`` plus ``"synthetic_reason"`` /
        ``"prior_turn_interrupt"`` / ``"prompt_index"`` only when they are not
        ``None`` (``skip_serializing_if = "Option::is_none"``). ``content`` is
        always emitted (it is the body, never skipped even when empty -- mirrors
        grok's no-``skip_serializing_if`` on the ``content`` field)."""
        payload: dict[str, Any] = {"content": [part.as_payload() for part in self.content]}
        if self.synthetic_reason is not None:
            payload["synthetic_reason"] = self.synthetic_reason.value
        if self.prior_turn_interrupt is not None:
            payload["prior_turn_interrupt"] = self.prior_turn_interrupt.value
        if self.prompt_index is not None:
            payload["prompt_index"] = self.prompt_index
        return payload


@dataclass(frozen=True, slots=True)
class AssistantItem:
    """An assistant-turn row of a conversation turn.

    ``content`` is the assistant text body (grok ``Arc<str>`` -> ``str``);
    ``tool_calls`` is the assistant-emitted tool invocations (grok
    ``Vec<ToolCall>`` -> ``tuple[ToolCall, ...]``); ``model_id`` is the model
    that produced the turn; ``model_fingerprint`` is the model fingerprint
    (aliases ``system_fingerprint`` on the wire + the
    :func:`empty_string_as_none` hook); ``reasoning_effort`` is the R206 strict
    enum. ``content`` is required (no ``#[serde(default)]``); the rest default
    (``tool_calls = ()``, the three options ``= None``). One of the four struct
    members of the (un-migrated) :class:`ConversationItem` tagged union."""

    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    model_id: str | None = None
    model_fingerprint: str | None = None
    reasoning_effort: ReasoningEffort | None = None

    @classmethod
    def from_payload(cls, raw: Any) -> AssistantItem:
        """Strict-required + tolerant-optional wire parser.

        ``content`` (string) is required. ``tool_calls`` (a list of tool-call
        dicts) -> a tuple of :class:`ToolCall`; a missing / null -> ``()``; a
        non-list -> ``ValueError``. ``model_id`` defaults to ``None`` when
        absent / null and must be a string when present. ``model_fingerprint``
        resolves the ``model_fingerprint`` key first, falls back to the
        ``system_fingerprint`` serde alias, then runs
        :func:`empty_string_as_none` (``""`` -> ``None``). ``reasoning_effort``
        defaults to ``None`` when absent / null and parses via the strict
        :meth:`ReasoningEffort.from_payload` (an unknown effort ->
        ``ValueError``). Extra keys are tolerated."""
        if not isinstance(raw, dict):
            raise ValueError(
                f"assistant item must be a dict, got {type(raw).__name__}"
            )
        content = raw.get("content")
        if not isinstance(content, str):
            raise ValueError(
                "assistant item 'content' must be a string, "
                f"got {type(content).__name__}"
            )
        tool_calls = _parse_tool_call_list(raw.get("tool_calls"), "assistant item 'tool_calls'")
        model_id = _parse_optional_str(raw.get("model_id"), "assistant item 'model_id'")
        model_fingerprint = _parse_fingerprint(raw)
        reasoning_effort_raw = raw.get("reasoning_effort")
        if reasoning_effort_raw is None:
            reasoning_effort: ReasoningEffort | None = None
        else:
            reasoning_effort = ReasoningEffort.from_payload(reasoning_effort_raw)
        return cls(
            content=content,
            tool_calls=tool_calls,
            model_id=model_id,
            model_fingerprint=model_fingerprint,
            reasoning_effort=reasoning_effort,
        )

    def as_payload(self) -> dict[str, Any]:
        """Wire serializer (mirrors serde Serialize).

        -> ``{"content": <content>}`` plus ``"tool_calls"`` only when non-empty
        (``skip_serializing_if = "Vec::is_empty"``) + ``"model_id"`` /
        ``"model_fingerprint"`` / ``"reasoning_effort"`` only when not ``None``
        (``skip_serializing_if = "Option::is_none"``)."""
        payload: dict[str, Any] = {"content": self.content}
        if self.tool_calls:
            payload["tool_calls"] = [call.as_payload() for call in self.tool_calls]
        if self.model_id is not None:
            payload["model_id"] = self.model_id
        if self.model_fingerprint is not None:
            payload["model_fingerprint"] = self.model_fingerprint
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort.value
        return payload


@dataclass(frozen=True, slots=True)
class ToolResultItem:
    """A tool-result row of a conversation turn.

    ``tool_call_id`` matches the :class:`AssistantItem.tool_calls` entry this
    result resolves; ``content`` is the result body (grok ``Arc<str>`` ->
    ``str``); ``images`` is an optional sequence of image content parts (grok
    ``Vec<ContentPart>`` -> ``tuple[ContentPart, ...]``). ``tool_call_id`` +
    ``content`` are required (no ``#[serde(default)]``); ``images`` defaults to
    ``()``. One of the four struct members of the (un-migrated)
    :class:`ConversationItem` tagged union."""

    tool_call_id: str
    content: str
    images: tuple[ContentPart, ...] = ()

    @classmethod
    def from_payload(cls, raw: Any) -> ToolResultItem:
        """Strict-required + tolerant-optional wire parser.

        ``tool_call_id`` (string) + ``content`` (string) are required.
        ``images`` (a list of content-part dicts) -> a tuple of
        :class:`ContentPart`; a missing / null -> ``()``; a non-list ->
        ``ValueError``. Extra keys are tolerated."""
        if not isinstance(raw, dict):
            raise ValueError(
                f"tool result item must be a dict, got {type(raw).__name__}"
            )
        tool_call_id = raw.get("tool_call_id")
        if not isinstance(tool_call_id, str):
            raise ValueError(
                "tool result item 'tool_call_id' must be a string, "
                f"got {type(tool_call_id).__name__}"
            )
        content = raw.get("content")
        if not isinstance(content, str):
            raise ValueError(
                "tool result item 'content' must be a string, "
                f"got {type(content).__name__}"
            )
        images = _parse_content_part_list(raw.get("images"), "tool result item 'images'")
        return cls(tool_call_id=tool_call_id, content=content, images=images)

    def as_payload(self) -> dict[str, Any]:
        """Wire serializer (mirrors serde Serialize).

        -> ``{"tool_call_id": <id>, "content": <content>}`` plus ``"images"``
        only when non-empty (``skip_serializing_if = "Vec::is_empty"``)."""
        payload: dict[str, Any] = {
            "tool_call_id": self.tool_call_id,
            "content": self.content,
        }
        if self.images:
            payload["images"] = [part.as_payload() for part in self.images]
        return payload


# ---------------------------------------------------------------------------
# Private parse helpers (DRY -- shared by the four structs' from_payload).
# ---------------------------------------------------------------------------


def _parse_content_part_list(raw: Any, label: str) -> tuple[ContentPart, ...]:
    """Tolerant ``Vec<ContentPart>`` parser -- a missing / null -> ``()`` (the
    ``#[derive(Default)]`` / ``#[serde(default)]`` surface); a list -> a tuple
    of :class:`ContentPart`; a non-list -> ``ValueError``."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{label} must be a list, got {type(raw).__name__}")
    return tuple(ContentPart.from_payload(part) for part in raw)


def _parse_tool_call_list(raw: Any, label: str) -> tuple[ToolCall, ...]:
    """Tolerant ``Vec<ToolCall>`` parser -- a missing / null -> ``()``; a list
    -> a tuple of :class:`ToolCall`; a non-list -> ``ValueError``."""
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"{label} must be a list, got {type(raw).__name__}")
    return tuple(ToolCall.from_payload(call) for call in raw)


def _parse_optional_enum(raw: Any, enum_cls: type[Any]) -> Any:
    """Tolerant ``Option<T>`` (catch-all enum) parser -- a missing / null ->
    ``None``; a present value -> ``enum_cls.from_payload(raw)`` (the catch-all
    enums never raise, so no label is needed for an error message)."""
    if raw is None:
        return None
    return enum_cls.from_payload(raw)


def _parse_optional_int(raw: Any, label: str) -> int | None:
    """Tolerant ``Option<usize>`` parser -- a missing / null -> ``None``; a
    non-int -> ``ValueError`` (``bool`` is rejected -- ``bool`` is an ``int``
    subclass in Python but not a valid ``usize`` on the wire)."""
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"{label} must be an int, got {type(raw).__name__}")
    return raw


def _parse_optional_str(raw: Any, label: str) -> str | None:
    """Tolerant ``Option<String>`` parser -- a missing / null -> ``None``; a
    non-string -> ``ValueError``."""
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError(f"{label} must be a string, got {type(raw).__name__}")
    return raw


def _parse_fingerprint(raw: dict[str, Any]) -> str | None:
    """Resolve ``model_fingerprint`` with its ``system_fingerprint`` serde alias
    + the :func:`empty_string_as_none` hook.

    Checks ``model_fingerprint`` first; if absent, falls back to the
    ``system_fingerprint`` alias; if neither is present -> ``None``. The
    resolved value must be a string or null (a non-string -> ``ValueError``);
    an empty string -> ``None`` (mirrors the grok ``deserialize_with =
    "empty_string_as_none"`` hook)."""
    if "model_fingerprint" in raw:
        fp_raw = raw["model_fingerprint"]
    elif "system_fingerprint" in raw:
        fp_raw = raw["system_fingerprint"]
    else:
        fp_raw = None
    if fp_raw is not None and not isinstance(fp_raw, str):
        raise ValueError(
            "assistant item 'model_fingerprint' must be a string or null, "
            f"got {type(fp_raw).__name__}"
        )
    return empty_string_as_none(fp_raw)


__all__ = [
    "AssistantItem",
    "SystemItem",
    "ToolResultItem",
    "UserItem",
]
