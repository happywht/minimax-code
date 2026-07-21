"""Anthropic Messages API (``/v1/messages``) wire types -- stop-reason + usage +
delta-body cluster (R201, ``xai-grok-sampling-types`` ``messages.rs``).

``messages.rs`` carries 26 line types for the Anthropic Messages API
request/response/streaming format. R201 lands the tolerant stop-reason enum +
the terminal ``message_delta`` body cluster -- the catch-all sites that must
never fail a stream when the server adds a new stop reason. The request types
(``MessagesRequest`` + ``ContentBlock`` union + full ``MessageStreamEvent``
wrapper) land in later rounds: they pull in the larger ``ContentBlock``
discriminated union and its 5 variants, which is a multi-round leaf on its own.

This module is no-I/O (``serde_json::Value`` -> ``dict``). Migration map
(grok -> Python):

- ``#[serde(rename_all="snake_case")] enum`` + ``#[serde(untagged)] Unknown(String)``
  catch-all -> frozen+slots discriminated union (base + 8 subclasses); the
  :class:`UnknownStopReason` variant preserves the wire string so a new
  server-side value can never fail the terminal parse. :func:`parse_stop_reason`
  / :func:`stop_reason_to_wire` mirror serde's try-tagged-first-then-untagged
  order (Unknown stays LAST).
- ``#[serde(default)] u32`` -> ``int`` field default ``0``; ``Option<u32>`` with
  ``#[serde(default)]`` -> ``int | None = None``.
- ``Option<T>`` + ``#[serde(skip_serializing_if)]`` -> ``T | None``.
- ``#[serde(rename="type")] r#type`` -> ``type_`` field (wire key ``"type"``);
  renamed to avoid shadowing the Python builtin.

YAGNI: full serde ``Serialize``/``Deserialize`` round-trip. :func:`stop_reason_to_wire`
covers the catch-all faithfulness the grok tests assert (the Unknown variant
must re-serialize the wire string verbatim); other types are shape + tolerant
``from_payload`` only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# StopReason: snake_case enum + Unknown(String) catch-all.
# ---------------------------------------------------------------------------
#
# serde tries the tagged (snake_case) variants first, then falls back to the
# ``#[serde(untagged)] Unknown(String)`` catch-all, so a new server-side value
# can never fail the terminal ``message_delta`` parse and discard an
# already-streamed response. Unknown is intentionally LAST in the dispatch
# table below to mirror that order.


@dataclass(frozen=True, slots=True)
class StopReason:
    """Terminal stop reason for a ``/v1/messages`` response (union base).

    Use :func:`parse_stop_reason` for the wire string -> variant mapping and
    :func:`stop_reason_to_wire` for the faithful inverse (the catch-all
    round-trips the wire string verbatim).
    """


@dataclass(frozen=True, slots=True)
class EndTurn(StopReason):
    """``end_turn`` -- the model finished its reply naturally."""


@dataclass(frozen=True, slots=True)
class MaxTokens(StopReason):
    """``max_tokens`` -- generation stopped at the token budget."""


@dataclass(frozen=True, slots=True)
class ToolUse(StopReason):
    """``tool_use`` -- the model requested one or more tool calls."""


@dataclass(frozen=True, slots=True)
class StopSequence(StopReason):
    """``stop_sequence`` -- a configured stop sequence matched."""


@dataclass(frozen=True, slots=True)
class Refusal(StopReason):
    """``refusal`` -- the request was blocked (see :class:`StopDetails`)."""


@dataclass(frozen=True, slots=True)
class PauseTurn(StopReason):
    """``pause_turn`` -- the model paused mid-turn (long-form tool use)."""


@dataclass(frozen=True, slots=True)
class ModelContextWindowExceeded(StopReason):
    """``model_context_window_exceeded`` -- the context window filled."""


@dataclass(frozen=True, slots=True)
class UnknownStopReason(StopReason):
    """Catch-all for a stop reason this client version does not know yet.

    Preserves the wire string for logging and faithful re-serialization; must
    stay the fallback (mirrors serde's ``#[serde(untagged)]`` last-variant
    semantics)."""

    value: str


# Known snake_case wire labels -> variant class. Unknown is intentionally NOT
# in this table; :func:`parse_stop_reason` falls back to UnknownStopReason.
_KNOWN_STOP_REASONS: dict[str, type[StopReason]] = {
    "end_turn": EndTurn,
    "max_tokens": MaxTokens,
    "tool_use": ToolUse,
    "stop_sequence": StopSequence,
    "refusal": Refusal,
    "pause_turn": PauseTurn,
    "model_context_window_exceeded": ModelContextWindowExceeded,
}
# Inverse map for the faithful wire emission of known variants.
_STOP_REASON_TO_WIRE: dict[type[StopReason], str] = {
    cls: wire for wire, cls in _KNOWN_STOP_REASONS.items()
}


def parse_stop_reason(raw: str) -> StopReason:
    """Parse a stop-reason wire string. Known snake_case values map to their
    variants; any other value falls back to :class:`UnknownStopReason`
    (mirrors serde's tagged-first-then-untagged order -- the catch-all stays
    LAST so a new server value never fails the terminal parse)."""
    cls = _KNOWN_STOP_REASONS.get(raw)
    if cls is not None:
        return cls()
    return UnknownStopReason(raw)


def stop_reason_to_wire(reason: StopReason) -> str:
    """Faithful inverse of :func:`parse_stop_reason`: known variants emit their
    snake_case wire label; :class:`UnknownStopReason` re-emits the preserved
    wire string verbatim (mirrors serde's catch-all re-serialization)."""
    if isinstance(reason, UnknownStopReason):
        return reason.value
    return _STOP_REASON_TO_WIRE[type(reason)]


# ---------------------------------------------------------------------------
# Token usage (non-streaming response + streaming terminal delta).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MessagesUsage:
    """Token usage for a non-streaming ``/v1/messages`` response.

    ``input_tokens`` / ``output_tokens`` are required on the wire; the two
    cache counters default to ``0`` (``#[serde(default)]``) since older
    backends omit them.
    """

    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> MessagesUsage:
        """Tolerant constructor mirroring ``#[serde(default)]``: missing cache
        counters fall back to ``0``."""
        return cls(
            input_tokens=payload.get("input_tokens", 0),
            output_tokens=payload.get("output_tokens", 0),
            cache_creation_input_tokens=payload.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=payload.get("cache_read_input_tokens", 0),
        )


@dataclass(frozen=True, slots=True)
class MessageDeltaUsage:
    """Token usage carried by a terminal ``message_delta`` event.

    Only ``output_tokens`` is required; the rest are optional
    (``#[serde(default)]`` on ``Option<u32>`` -- absent rather than ``0`` when
    the server omits them)."""

    output_tokens: int
    input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    cache_creation_input_tokens: int | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> MessageDeltaUsage:
        """Tolerant constructor: only ``output_tokens`` defaults (to ``0``);
        the optional counters stay ``None`` when absent."""
        return cls(
            output_tokens=payload.get("output_tokens", 0),
            input_tokens=payload.get("input_tokens"),
            cache_read_input_tokens=payload.get("cache_read_input_tokens"),
            cache_creation_input_tokens=payload.get("cache_creation_input_tokens"),
        )


# ---------------------------------------------------------------------------
# Terminal message_delta body: stop reason + provider detail.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StopDetails:
    """Provider detail for a terminal ``message_delta`` stop, e.g.
    ``{"type":"refusal","category":"frontier_llm","explanation":"..."}`` on an
    Anthropic ToS auto-refusal. All fields optional so an unknown shape never
    fails the terminal parse (``#[serde(default)]`` on every field)."""

    type_: str | None = None
    category: str | None = None
    explanation: str | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> StopDetails:
        """Tolerant constructor: every field is optional and unknown keys are
        ignored (forwards-compat with future detail shapes)."""
        return cls(
            type_=payload.get("type"),
            category=payload.get("category"),
            explanation=payload.get("explanation"),
        )


@dataclass(frozen=True, slots=True)
class MessageDeltaBody:
    """The ``delta`` body of a terminal ``message_delta`` event.

    ``stop_reason`` is optional + tolerant (a future stop reason yields
    :class:`UnknownStopReason`, never an error); ``stop_details`` is optional
    (absent on the wire unless the stop carries provider detail, e.g. a
    refusal explanation).
    """

    stop_reason: StopReason | None = None
    stop_details: StopDetails | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> MessageDeltaBody:
        """Tolerant constructor: ``stop_reason`` parses through the catch-all
        (only when a string is on the wire); ``stop_details`` is built only
        when a dict is present. Unknown keys (e.g. ``stop_sequence``) are
        ignored."""
        raw_reason = payload.get("stop_reason")
        stop_reason = parse_stop_reason(raw_reason) if isinstance(raw_reason, str) else None
        raw_details = payload.get("stop_details")
        stop_details = StopDetails.from_payload(raw_details) if isinstance(raw_details, dict) else None
        return cls(stop_reason=stop_reason, stop_details=stop_details)


# ---------------------------------------------------------------------------
# Streaming error event payload.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StreamError:
    """Error payload carried by a streaming ``error`` event."""

    type_: str
    message: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> StreamError:
        """Tolerant constructor: missing ``type`` / ``message`` default to the
        empty string (strict grok would fail; the platform never crashes a
        stream on a malformed error frame)."""
        return cls(
            type_=payload.get("type", ""),
            message=payload.get("message", ""),
        )


__all__ = [
    "EndTurn",
    "MaxTokens",
    "MessageDeltaBody",
    "MessageDeltaUsage",
    "MessagesUsage",
    "ModelContextWindowExceeded",
    "PauseTurn",
    "Refusal",
    "StopDetails",
    "StopReason",
    "StopSequence",
    "StreamError",
    "ToolUse",
    "UnknownStopReason",
    "parse_stop_reason",
    "stop_reason_to_wire",
]
