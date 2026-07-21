"""Conversation-layer tool choice (R220, ``xai-grok-sampling-types``
``conversation.rs``).

R220 lands :class:`ConversationToolChoice` (``conversation.rs`` ~588) -- the
API-agnostic conversation-layer tool-choice tagged union. This is the sampler
package's first **externally-tagged mixed enum** -- the third serde
representation shape, after R84's ``untagged`` :class:`JsonRpcId` and R89's
``internally-tagged`` (``tag = "type"``) :class:`HookEvent`. ``#[serde(rename_all
= "snake_case")]`` + serde's default external tagging means each variant
serializes at the JSON edge as either a bare snake_case string (the three
field-less variants) or a single-key object mapping the snake_case variant name
to its payload (the ``Function(String)`` newtype variant):

- ``Auto`` -> ``"auto"`` (the model decides whether to use tools)
- ``None`` -> ``"none"`` (the model must not use tools)
- ``Required`` -> ``"required"`` (the model must use a tool)
- ``Function("tool_name")`` -> ``{"function": "tool_name"}``
  (the model must use a specific tool)

This is the standard OpenAI ``tool_choice`` wire shape, hoisted into the
conversation layer (the API-agnostic internal representation). Distinct from the
already-migrated types.rs wire-layer :class:`ToolChoice` family
(:class:`ToolChoice` / :class:`FunctionToolChoice` / :class:`AutoToolChoiceParam`
/ :class:`ToolChoiceParam` ...): that family is the ChatCompletions wire
surface; this one is the conversation-internal peer -- mirrors the ``Usage`` ->
:class:`ChatUsage` (R207) and ``StopReason`` -> :class:`ConversationStopReason`
(R219) layer-split precedents.

Dependency closure: zero external. The three field-less variants carry no
state; ``Function`` carries a single ``String`` (Python ``str``). No sampler
types referenced. The barrel collision with the wire-layer
:class:`FunctionToolChoice` is dodged by the uniform ``Conversation`` variant
prefix (:class:`ConversationAuto` / :class:`ConversationFunction` /
:class:`ConversationNone` / :class:`ConversationRequired`), mirroring the
:class:`ConversationStopReason` rename.

This module is no-I/O (pure value-level). Migration map (grok -> Python):

- ``enum ConversationToolChoice { Auto, None, Required, Function(String) }``
  -> :class:`ConversationToolChoice` frozen+slots union base carrying the
  hand-written externally-tagged (de)serializer pair :meth:`from_payload` /
  :meth:`as_payload` (mirrors the ``#[derive(Serialize, Deserialize)]``).
- ``Auto`` -> :class:`ConversationAuto` (field-less).
- ``None`` -> :class:`ConversationNone` (field-less).
- ``Required`` -> :class:`ConversationRequired` (field-less).
- ``Function(String)`` -> :class:`ConversationFunction` (``name: str``).

YAGNI: none -- every variant plus both serde directions land together (the enum
crosses the wire, unlike the R217 :class:`DanglingToolCallReason` in-program
union which carries no serde at all).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ConversationToolChoice:
    """How the model should choose tools (externally-tagged tagged union).

    The conversation-layer tool choice -- API-agnostic, hoisted from the
    OpenAI ``tool_choice`` wire shape. Four variants: three field-less
    (:class:`ConversationAuto` / :class:`ConversationNone` /
    :class:`ConversationRequired`) plus one data-carrying
    (:class:`ConversationFunction`, the specific-tool pin). Distinct from the
    wire-layer :class:`ToolChoice` family (types.rs, ChatCompletions surface);
    build one via :meth:`from_payload` and inspect one via :meth:`as_payload`.
    """

    @classmethod
    def from_payload(cls, raw: Any) -> ConversationToolChoice:
        """Externally-tagged wire parser (mirrors serde Deserialize).

        A bare snake_case string -> the matching field-less variant; a
        single-key ``{"function": <name>}`` object (``name`` a ``str``) ->
        :class:`ConversationFunction`; anything else -> ``ValueError``. The
        enum has no ``#[serde(other)]`` catch-all, so an unknown tag, a
        malformed object, or a non-string function name fails -- strict parity
        with the R219 :class:`ConversationStopReason` (contrast the R218
        ``UNKNOWN`` catch-all enums which never raise)."""
        if isinstance(raw, str):
            if raw == "auto":
                return ConversationAuto()
            if raw == "none":
                return ConversationNone()
            if raw == "required":
                return ConversationRequired()
            raise ValueError(f"unknown conversation tool choice tag: {raw!r}")
        if isinstance(raw, dict):
            # Externally-tagged newtype variant: exactly the single "function"
            # key, whose value must be a string (grok ``Function(String)``
            # rejects a non-string payload).
            if len(raw) == 1 and "function" in raw:
                name = raw["function"]
                if not isinstance(name, str):
                    raise ValueError(
                        "conversation tool choice 'function' must be a string, "
                        f"got {type(name).__name__}"
                    )
                return ConversationFunction(name=name)
            raise ValueError(f"malformed conversation tool choice object: {raw!r}")
        raise ValueError(
            "conversation tool choice must be a str or dict, "
            f"got {type(raw).__name__}"
        )

    def as_payload(self) -> Any:
        """Externally-tagged wire serializer (mirrors serde Serialize).

        The field-less variants -> their bare snake_case string;
        :class:`ConversationFunction` -> ``{"function": name}``."""
        if isinstance(self, ConversationFunction):
            return {"function": self.name}
        if isinstance(self, ConversationAuto):
            return "auto"
        if isinstance(self, ConversationNone):
            return "none"
        if isinstance(self, ConversationRequired):
            return "required"
        raise TypeError(f"unknown ConversationToolChoice variant: {self!r}")


@dataclass(frozen=True, slots=True)
class ConversationAuto(ConversationToolChoice):
    """The model decides whether to use tools (wire ``"auto"``)."""


@dataclass(frozen=True, slots=True)
class ConversationNone(ConversationToolChoice):
    """The model must not use tools (wire ``"none"``)."""


@dataclass(frozen=True, slots=True)
class ConversationRequired(ConversationToolChoice):
    """The model must use a tool (wire ``"required"``)."""


@dataclass(frozen=True, slots=True)
class ConversationFunction(ConversationToolChoice):
    """The model must use a specific tool (wire ``{"function": name}``).

    ``name`` is the function (tool) name the model is pinned to."""

    name: str


__all__ = [
    "ConversationAuto",
    "ConversationFunction",
    "ConversationNone",
    "ConversationRequired",
    "ConversationToolChoice",
]
