"""OpenAI ChatCompletion atomic leaf types (R206,
``xai-grok-sampling-types`` ``types.rs``).

R206 lands the first slice of ``types.rs`` -- the zero-dependency atomic
leaves of the OpenAI-compatible ChatCompletion type family: 4 wire-string
enums (``Role`` / ``ToolType`` / ``FinishReason`` / ``ReasoningEffort``) + 5
flat leaf structs (``ImageUrl`` / ``ToolChoiceFunction`` / ``ToolCallFunction``
/ ``PromptTokensDetails`` / ``CompletionTokensDetails``). These are the
building blocks the ChatCompletion request / response / streaming containers
(``ChatCompletionRequest`` / ``ChatCompletionResponse`` / ``ChatCompletionChunk``
+ their inner shapes) consume; those heavier containers + the
``TraceContext`` trait + the list-carrying mid-layer (``ChatRequestMessage`` /
``MessageContent`` / ``ChatContentBlock`` / ``Usage`` / ``SearchParameters`` /
``SearchSource`` + the compaction enums + the reasoning-effort meta helpers +
``ApiBackend`` / ``SamplingConfig`` / ``CreateResponseWrapper`` /
``MessagesRequestWrapper``) land in later rounds -- they depend on each other
/ on the ``xai-grok-tools`` re-exports (``ToolDefinition`` / ``FunctionTool``)
/ on ``crate::rs`` / on ``crate::serde_helpers``, so they are NOT zero-
dependency leaves.

Dependency closure: zero external (no ``crate::rs``, no ``xai-grok-tools``,
no ``serde_helpers``). All 9 types are self-contained -- a clean first slice.

This module is no-I/O (``serde_json::Value`` -> ``dict`` / wire string).
Migration map (grok -> Python):

- ``#[serde(rename_all="lowercase")] enum`` (``Role`` / ``ToolType`` /
  ``ReasoningEffort``) -> :class:`enum.StrEnum` with the lowercase wire values.
- ``#[serde(rename_all="snake_case")] enum`` (``FinishReason``) ->
  :class:`enum.StrEnum` with the snake_case wire values.
- ``#[default]`` (``ReasoningEffort::Medium``) -> the
  :data:`DEFAULT_REASONING_EFFORT` constant.
- plain ``#[derive(Serialize, Deserialize)] struct`` (``ImageUrl`` /
  ``ToolChoiceFunction`` / ``ToolCallFunction``) -> ``@dataclass(frozen=True,
  slots=True)`` with a tolerant ``from_payload``.
- ``#[derive(Default)] struct`` (``PromptTokensDetails`` /
  ``CompletionTokensDetails``) -> ``@dataclass(frozen=True, slots=True)`` + a
  ``default()`` classmethod + ``from_payload``.
- ``ToolCallFunction::from_json(name, value: &Value)`` ->
  :meth:`ToolCallFunction.from_json` (a classmethod that ``json.dumps`` the
  structured value into the ``arguments`` string, mirroring
  ``serde_json::Value::to_string``).

Strict enum parse: grok's ``Role`` / ``ToolType`` / ``FinishReason`` /
``ReasoningEffort`` carry NO ``#[serde(other)]`` catch-all, so an unknown wire
string or a non-string raises ``ValueError`` (mirrors serde's enum failure on
these catch-all-less enums -- contrast the R201 :class:`StopReason` catch-all
which must never fail a terminal stream). ``from_payload`` on each enum is
the strict single-string parser; the enum is also directly constructable via
``Cls("wire_value")`` (the inherited :class:`enum.StrEnum` value lookup).

Naming: the grok ``ReasoningEffort::None`` variant maps to
``ReasoningEffort.NONE`` (``None`` is a Python keyword, so the member is
upper-cased). ``types.rs`` ``Role`` is distinct from the R203
:class:`MessageRole` (an Anthropic Messages API 2-variant lowercase enum);
the two never collide in the package barrel (``Role`` is new).

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip -- ``from_payload``
covers the parse direction the platform needs. The ``ReasoningEffort``
meta-config layer (``to_responses_api`` + ``parse_canonical_effort_token`` /
``supports_reasoning_effort_meta`` / ``parse_reasoning_effort_meta`` /
``reasoning_effort_meta_value`` / ``parse_reasoning_effort_options`` /
``parse_reasoning_efforts_meta`` / ``reasoning_efforts_meta_value`` +
``ReasoningEffortOption`` + 3 consts) lands with the ``SamplingConfig``
cluster in a later round -- it is a meta-config layer, not an atomic leaf.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

_E = TypeVar("_E", bound=StrEnum)


def _parse_strict_enum(cls: type[_E], raw: object, label: str) -> _E:
    """Strict wire-string -> :class:`enum.StrEnum` (no catch-all).

    An unknown wire string or a non-string raises ``ValueError`` (mirrors
    serde's enum failure on these ``#[serde(other)]``-less enums -- contrast
    the R201 :class:`StopReason` catch-all which must never fail a terminal
    stream). Used by every enum ``from_payload`` below."""
    if not isinstance(raw, str):
        raise ValueError(f"{label} wire value must be a string, got {type(raw).__name__}")
    try:
        return cls(raw)
    except ValueError as exc:
        raise ValueError(f"unknown {label} wire value: {raw!r}") from exc


# ---------------------------------------------------------------------------
# Role: ChatCompletion message role (#[serde(rename_all="lowercase")]).
# ---------------------------------------------------------------------------
#
# 4 variants: System / User / Assistant / Tool. Distinct from the R203
# MessageRole (an Anthropic Messages API 2-variant lowercase enum) -- the two
# never collide in the package barrel.


class Role(StrEnum):
    """ChatCompletion message role (``#[serde(rename_all="lowercase")]``).

    ``System`` -> ``"system"``; ``User`` -> ``"user"``; ``Assistant`` ->
    ``"assistant"``; ``Tool`` -> ``"tool"`` (lowercase wire values). Distinct
    from the R203 :class:`MessageRole` (an Anthropic Messages API 2-variant
    lowercase enum)."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"

    @classmethod
    def from_payload(cls, raw: object) -> Role:
        """Strict wire-string parser (no catch-all -- an unknown role / non-string
        raises ``ValueError``)."""
        return _parse_strict_enum(cls, raw, "role")


# ---------------------------------------------------------------------------
# ToolType: tool discriminator (#[serde(rename_all="lowercase")]).
# ---------------------------------------------------------------------------
#
# A single Function variant -- grok ships a 1-variant enum as the discriminator
# reused across tool choice / tool call kinds.


class ToolType(StrEnum):
    """Tool type discriminator (``#[serde(rename_all="lowercase")]``).

    ``Function`` -> ``"function"`` (the only variant; the discriminator reused
    across tool choice / tool call kinds)."""

    FUNCTION = "function"

    @classmethod
    def from_payload(cls, raw: object) -> ToolType:
        """Strict wire-string parser (no catch-all)."""
        return _parse_strict_enum(cls, raw, "tool type")


# ---------------------------------------------------------------------------
# FinishReason: ChatCompletion finish reason (#[serde(rename_all="snake_case")]).
# ---------------------------------------------------------------------------


class FinishReason(StrEnum):
    """ChatCompletion finish reason (``#[serde(rename_all="snake_case")]``).

    ``Stop`` -> ``"stop"``; ``Length`` -> ``"length"``; ``ToolCalls`` ->
    ``"tool_calls"``; ``ContentFilter`` -> ``"content_filter"``;
    ``FunctionCall`` -> ``"function_call"`` (snake_case wire values). No
    catch-all -- an unknown reason raises."""

    STOP = "stop"
    LENGTH = "length"
    TOOL_CALLS = "tool_calls"
    CONTENT_FILTER = "content_filter"
    FUNCTION_CALL = "function_call"

    @classmethod
    def from_payload(cls, raw: object) -> FinishReason:
        """Strict wire-string parser (no catch-all)."""
        return _parse_strict_enum(cls, raw, "finish reason")


# ---------------------------------------------------------------------------
# ReasoningEffort: reasoning effort level (lowercase, #[default] Medium).
# ---------------------------------------------------------------------------
#
# 6 variants. None / Minimal are omitted on the Anthropic Messages API (a
# serialization behavior -- YAGNI for the parse direction). The grok `None`
# variant maps to NONE (None is a Python keyword). #[default] Medium ->
# DEFAULT_REASONING_EFFORT.


class ReasoningEffort(StrEnum):
    """Reasoning effort level (``#[serde(rename_all="lowercase")]``,
    ``#[default] Medium``).

    ``None`` -> ``"none"``; ``Minimal`` -> ``"minimal"``; ``Low`` -> ``"low"``;
    ``Medium`` -> ``"medium"`` (default); ``High`` -> ``"high"``; ``Xhigh`` ->
    ``"xhigh"``. The grok ``None`` variant maps to :attr:`NONE` (``None`` is a
    Python keyword). ``None`` / ``Minimal`` are omitted on the Anthropic
    Messages API (a serialization behavior -- YAGNI for the parse direction)."""

    NONE = "none"
    MINIMAL = "minimal"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"

    @classmethod
    def from_payload(cls, raw: object) -> ReasoningEffort:
        """Strict wire-string parser (no catch-all)."""
        return _parse_strict_enum(cls, raw, "reasoning effort")


DEFAULT_REASONING_EFFORT = ReasoningEffort.MEDIUM
"""Mirror of grok ``#[default] ReasoningEffort::Medium``."""


# ---------------------------------------------------------------------------
# ImageUrl: image URL leaf (the ChatContentBlock::ImageUrl payload).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ImageUrl:
    """Image URL leaf (the ``ChatContentBlock::ImageUrl`` payload).

    A single ``url`` field. The grok struct is NOT ``#[derive(Default)]`` (the
    field is required on the wire), but ``from_payload`` tolerates a missing /
    null / non-dict value as the empty string (the platform never fails a
    parse on a malformed image block)."""

    url: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> ImageUrl:
        """Tolerant constructor: ``url`` defaults to the empty string when the
        payload is missing / null / not a dict / lacks the key."""
        if not isinstance(payload, dict):
            return cls()
        return cls(url=payload.get("url", ""))


# ---------------------------------------------------------------------------
# ToolChoiceFunction: named-function tool choice (the ToolChoice::Function payload).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolChoiceFunction:
    """Named-function tool choice (the ``ToolChoice::Function`` payload).

    A single ``name`` field naming the tool to call."""

    name: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> ToolChoiceFunction:
        """Tolerant constructor: ``name`` defaults to the empty string when the
        payload is missing / null / not a dict / lacks the key."""
        if not isinstance(payload, dict):
            return cls()
        return cls(name=payload.get("name", ""))


# ---------------------------------------------------------------------------
# ToolCallFunction: a tool call's function name + serialized arguments.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolCallFunction:
    """A tool call's function name + serialized arguments.

    ``arguments`` is a JSON string (the model streams a partial JSON fragment
    during streaming, a complete JSON object when non-streaming). Use
    :meth:`from_json` to build it from a structured value (mirrors grok's
    ``from_json(name, value: &Value)`` -> ``arguments: value.to_string()``)."""

    name: str = ""
    arguments: str = ""

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> ToolCallFunction:
        """Tolerant constructor: ``name`` / ``arguments`` default to the empty
        string when the payload is missing / null / not a dict / lacks the
        keys."""
        if not isinstance(payload, dict):
            return cls()
        return cls(name=payload.get("name", ""), arguments=payload.get("arguments", ""))

    @classmethod
    def from_json(cls, name: str, value: Any) -> ToolCallFunction:
        """Build from a structured value: ``arguments`` becomes
        ``json.dumps(value)`` (mirrors ``serde_json::Value::to_string`` -- a
        string is quoted, a dict is serialized, a number is bare)."""
        return cls(name=name, arguments=json.dumps(value))


# ---------------------------------------------------------------------------
# PromptTokensDetails: prompt token usage breakdown (#[derive(Default)]).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PromptTokensDetails:
    """Prompt token usage breakdown (``#[derive(Default)]``).

    ``cached_tokens`` / ``audio_tokens`` both default to 0."""

    cached_tokens: int = 0
    audio_tokens: int = 0

    @classmethod
    def default(cls) -> PromptTokensDetails:
        """Mirror ``#[derive(Default)]``: both fields 0."""
        return cls()

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> PromptTokensDetails:
        """Tolerant constructor: each field defaults to 0 when the payload is
        missing / null / not a dict / lacks the key."""
        if not isinstance(payload, dict):
            return cls()
        return cls(
            cached_tokens=payload.get("cached_tokens", 0),
            audio_tokens=payload.get("audio_tokens", 0),
        )


# ---------------------------------------------------------------------------
# CompletionTokensDetails: completion token usage breakdown (#[derive(Default)]).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CompletionTokensDetails:
    """Completion token usage breakdown (``#[derive(Default)]``).

    ``reasoning_tokens`` / ``audio_tokens`` / ``accepted_prediction_tokens`` /
    ``rejected_prediction_tokens`` all default to 0."""

    reasoning_tokens: int = 0
    audio_tokens: int = 0
    accepted_prediction_tokens: int = 0
    rejected_prediction_tokens: int = 0

    @classmethod
    def default(cls) -> CompletionTokensDetails:
        """Mirror ``#[derive(Default)]``: all four fields 0."""
        return cls()

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> CompletionTokensDetails:
        """Tolerant constructor: each field defaults to 0 when the payload is
        missing / null / not a dict / lacks the key."""
        if not isinstance(payload, dict):
            return cls()
        return cls(
            reasoning_tokens=payload.get("reasoning_tokens", 0),
            audio_tokens=payload.get("audio_tokens", 0),
            accepted_prediction_tokens=payload.get("accepted_prediction_tokens", 0),
            rejected_prediction_tokens=payload.get("rejected_prediction_tokens", 0),
        )


__all__ = [
    "CompletionTokensDetails",
    "DEFAULT_REASONING_EFFORT",
    "FinishReason",
    "ImageUrl",
    "PromptTokensDetails",
    "ReasoningEffort",
    "Role",
    "ToolCallFunction",
    "ToolChoiceFunction",
    "ToolType",
]
