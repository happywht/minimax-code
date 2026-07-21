"""Anthropic Messages API request-side enums + leaf structs (R203,
``xai-grok-sampling-types`` ``messages.rs``).

R203 lands the request-side enums + simple leaf structs -- the
``MessagesRequest`` container's direct dependencies that carry no
list-of-:class:`ContentBlock` recursion (those -- ``MessagesRequest`` +
``Message`` + ``MessageContent`` + ``SystemParam`` + the standalone
``TextBlock`` struct -- land in later rounds, consuming the R202
:class:`ContentBlock` union). The 8 type families here are pure enums + flat
structs (``serde_json::Value`` -> ``dict``), no I/O.

Migration map (grok -> Python):

- ``#[serde(rename_all="lowercase")] enum`` (:class:`MessageRole`) ->
  :class:`enum.StrEnum` with the lowercase wire values.
- ``#[serde(rename_all="snake_case")] enum`` (:class:`ThinkingDisplay`) ->
  :class:`enum.StrEnum` with the snake_case wire values.
- ``#[serde(tag="type", rename_all="snake_case")] enum`` (:class:`ThinkingConfig`
  / :class:`OutputFormat` / :class:`ToolChoiceParam`) -> frozen+slots union base
  + subclasses; ``from_payload`` dispatches on the wire ``type`` tag. The unions
  have NO catch-all, so an unknown tag raises ``ValueError`` (request-side strict
  parse, mirroring serde's tagged-union semantics -- contrast the R201
  :class:`StopReason` catch-all which must never fail a terminal stream).
- Unit variants (``Disabled`` / ``Auto`` / ``Any``) -> fieldless frozen+slots
  subclasses (mirrors the R201 ``EndTurn`` / ``MaxTokens`` unit-variant shape).
- ``serde_json::Value`` -> ``Any`` (:class:`OutputFormat` ``schema``,
  :class:`ToolParam` ``input_schema``).
- ``#[serde(skip_serializing_if="Option::is_none")]`` -> ``T | None = None``.

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip -- ``from_payload``
covers the parse direction the platform needs. The list-carrying containers
(``MessagesRequest`` + ``Message`` + ``MessageContent`` + ``SystemParam`` + the
standalone ``TextBlock`` struct) and the response/streaming containers
(``MessagesResponse`` + ``MessageStreamEvent`` wrapper + ``StreamDelta``) consume
the R202 union but are separate layers landing in later rounds.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# ---------------------------------------------------------------------------
# MessageRole: lowercase enum (User/Assistant -> "user"/"assistant").
# ---------------------------------------------------------------------------


class MessageRole(StrEnum):
    """Message author role (``#[serde(rename_all="lowercase")]``).

    ``User`` -> ``"user"``; ``Assistant`` -> ``"assistant"`` (lowercase wire
    values, not snake_case)."""

    USER = "user"
    ASSISTANT = "assistant"


# ---------------------------------------------------------------------------
# ThinkingDisplay: snake_case enum (Omitted/Summarized).
# ---------------------------------------------------------------------------


class ThinkingDisplay(StrEnum):
    """Extended-thinking display mode (``#[serde(rename_all="snake_case")]``,
    ``#[derive(Copy)]``).

    ``Omitted`` -> ``"omitted"``; ``Summarized`` -> ``"summarized"``."""

    OMITTED = "omitted"
    SUMMARIZED = "summarized"


# ---------------------------------------------------------------------------
# ThinkingConfig: 3-variant tagged union (tag="type", rename_all="snake_case").
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ThinkingConfig:
    """Extended-thinking configuration union base (tagged on the wire ``type``).
    Three modes per the Anthropic Messages API: ``enabled`` (explicit budget,
    4.0-4.5 models), ``adaptive`` (API decides budget, 4.6+ models), ``disabled``
    (off). Use :meth:`from_payload` for the wire dict -> variant mapping."""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ThinkingConfig:
        """Dispatch on the wire ``type`` tag. Known tags (``enabled`` /
        ``adaptive`` / ``disabled``) map to their variant; an unknown tag raises
        ``ValueError`` (mirrors serde's strict tagged-union parse -- no
        catch-all)."""
        kind = payload.get("type")
        if kind == "enabled":
            raw_budget = payload.get("budget_tokens")
            return EnabledThinkingConfig(
                budget_tokens=int(raw_budget) if raw_budget is not None else 0
            )
        if kind == "adaptive":
            raw_display = payload.get("display")
            display = (
                ThinkingDisplay(raw_display) if isinstance(raw_display, str) else None
            )
            return AdaptiveThinkingConfig(display=display)
        if kind == "disabled":
            return DisabledThinkingConfig()
        raise ValueError(f"unknown thinking config type: {kind!r}")


@dataclass(frozen=True, slots=True)
class EnabledThinkingConfig(ThinkingConfig):
    """``{"type":"enabled","budget_tokens":N}`` -- explicit thinking budget
    (4.0-4.5 models)."""

    budget_tokens: int


@dataclass(frozen=True, slots=True)
class AdaptiveThinkingConfig(ThinkingConfig):
    """``{"type":"adaptive","display":...?}`` -- the API decides the budget
    (4.6+ models). ``display`` is optional (``Option<ThinkingDisplay>``); newer
    thinking-capable models omit thinking content unless ``summarized``."""

    display: ThinkingDisplay | None = None


@dataclass(frozen=True, slots=True)
class DisabledThinkingConfig(ThinkingConfig):
    """``{"type":"disabled"}`` -- thinking off (pre-thinking models or
    ``thinking_budget=0``). Fieldless unit variant."""


# ---------------------------------------------------------------------------
# OutputFormat: 1-variant tagged union (tag="type", rename_all="snake_case").
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutputFormat:
    """Output-format union base (tagged on the wire ``type``). Use
    :meth:`from_payload` for the wire dict -> variant mapping."""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> OutputFormat:
        """Dispatch on the wire ``type`` tag. The only known tag today is
        ``json_schema``; an unknown tag raises ``ValueError`` (strict
        tagged-union parse -- forwards-compat via explicit failure, not a silent
        catch-all)."""
        kind = payload.get("type")
        if kind == "json_schema":
            return JsonSchemaOutputFormat(schema=payload.get("schema"))
        raise ValueError(f"unknown output format type: {kind!r}")


@dataclass(frozen=True, slots=True)
class JsonSchemaOutputFormat(OutputFormat):
    """``{"type":"json_schema","schema":{...}}`` -- a JSON Schema the response
    must conform to. ``schema`` is arbitrary JSON (``serde_json::Value``)."""

    schema: Any = None


# ---------------------------------------------------------------------------
# OutputConfig: leaf struct (effort + optional OutputFormat).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OutputConfig:
    """Response output configuration (``effort`` + optional ``format``).

    Both fields are optional (``#[serde(skip_serializing_if="Option::is_none")]``);
    ``format`` parses through :meth:`OutputFormat.from_payload` only when a dict
    is on the wire."""

    effort: str | None = None
    format: OutputFormat | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> OutputConfig:
        """Tolerant constructor: ``effort`` defaults to ``None``; ``format``
        parses through :meth:`OutputFormat.from_payload` only when a dict is
        present."""
        raw_format = payload.get("format")
        return cls(
            effort=payload.get("effort"),
            format=OutputFormat.from_payload(raw_format) if isinstance(raw_format, dict) else None,
        )


# ---------------------------------------------------------------------------
# ToolChoiceParam: 3-variant tagged union (tag="type", rename_all="snake_case").
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolChoiceParam:
    """Tool-choice union base (tagged on the wire ``type``). Use
    :meth:`from_payload` for the wire dict -> variant mapping."""

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ToolChoiceParam:
        """Dispatch on the wire ``type`` tag. Known tags (``auto`` / ``any`` /
        ``tool``) map to their variant; an unknown tag raises ``ValueError``
        (strict tagged-union parse -- no catch-all)."""
        kind = payload.get("type")
        if kind == "auto":
            return AutoToolChoiceParam()
        if kind == "any":
            return AnyToolChoiceParam()
        if kind == "tool":
            return NamedToolChoiceParam(name=payload.get("name", ""))
        raise ValueError(f"unknown tool choice type: {kind!r}")


@dataclass(frozen=True, slots=True)
class AutoToolChoiceParam(ToolChoiceParam):
    """``{"type":"auto"}`` -- the model decides whether to call a tool.
    Fieldless unit variant."""


@dataclass(frozen=True, slots=True)
class AnyToolChoiceParam(ToolChoiceParam):
    """``{"type":"any"}`` -- the model must call one of the provided tools.
    Fieldless unit variant."""


@dataclass(frozen=True, slots=True)
class NamedToolChoiceParam(ToolChoiceParam):
    """``{"type":"tool","name":...}`` -- the model must call the named tool
    (serde variant ``Tool { name }``)."""

    name: str


# ---------------------------------------------------------------------------
# ToolParam: leaf struct (name + description + input_schema).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ToolParam:
    """A tool definition in the Anthropic Messages API request.

    ``input_schema`` is arbitrary JSON (``serde_json::Value`` -- the tool's JSON
    Schema). Field order is reordered vs grok (``description`` is optional and so
    lands last with a default) but the wire mapping is name-based, not
    positional."""

    name: str
    input_schema: Any
    description: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ToolParam:
        """Tolerant constructor: ``name`` defaults to the empty string and
        ``input_schema`` to ``None`` when absent; ``description`` stays ``None``
        unless present."""
        return cls(
            name=payload.get("name", ""),
            input_schema=payload.get("input_schema"),
            description=payload.get("description"),
        )


# ---------------------------------------------------------------------------
# Metadata: leaf struct (user_id).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Metadata:
    """Request metadata. Only ``user_id`` is defined today; the field is optional
    (``#[serde(skip_serializing_if="Option::is_none")]``)."""

    user_id: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Metadata:
        """Tolerant constructor: ``user_id`` stays ``None`` unless present."""
        return cls(user_id=payload.get("user_id"))


__all__ = [
    "AdaptiveThinkingConfig",
    "AnyToolChoiceParam",
    "AutoToolChoiceParam",
    "DisabledThinkingConfig",
    "EnabledThinkingConfig",
    "JsonSchemaOutputFormat",
    "MessageRole",
    "Metadata",
    "NamedToolChoiceParam",
    "OutputConfig",
    "OutputFormat",
    "ThinkingConfig",
    "ThinkingDisplay",
    "ToolChoiceParam",
    "ToolParam",
]
