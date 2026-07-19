"""Session lifecycle events (R90).

Fusion of grok-build's ``xai-tool-protocol::session_event`` — the three
session-lifecycle types that ride inside ``ToolNotificationFrame`` as
``Custom`` notifications with ``kind = "session_event"``:
:class:`SessionEvent` (the lifecycle-event union), :class:`ToolCallOutcome`
(per-call result), :class:`SessionPhase` (lifecycle phase). They provide a
unified view of turn / tool activity across both samplers once the emitting
side is wired up.

Five crate-first serde shapes land here:

1. **``#[serde(other)]`` forward-compat catch-all — the crate's first.**
   :class:`SessionEvent` (internally-tagged, unit ``Unknown`` arm) AND the
   two string-enums :class:`ToolCallOutcome` / :class:`SessionPhase`
   (``Unknown`` arm) all carry a catch-all: an older consumer that meets a
   new ``event_type`` / outcome / phase value deserialises it as ``Unknown``
   instead of failing. Consumers MUST silently ignore ``Unknown`` events.
   This is the strict↔tolerant turning point of the crate — contrast
   :class:`~minimax_code.tool_protocol.turn_hook.TurnHookOutcome` (R90, same
   round, strict — no ``other`` arm; unknown values raise).
2. **``#[serde(default)]`` field-level default — the crate's first.**
   :attr:`TurnStarted.yolo_mode` defaults to ``False`` when the wire omits
   it. (Serialisation still always emits the field; ``default`` is
   deserialise-only.)
3. **``event_type`` tag key — the 5th tag-key name.** Prior enums used
   ``code`` / ``kind`` / ``shape`` / ``type`` (R83 / R88 / R89).
4. **Nested enum fields.** :attr:`TurnEnded.outcome` is a
   :class:`~minimax_code.tool_protocol.turn_hook.TurnHookOutcome`,
   :attr:`ToolCallCompleted.outcome` is a :class:`ToolCallOutcome`,
   :attr:`PhaseChanged.phase` is a :class:`SessionPhase` — the crate's
   first enum fields nested inside another enum's struct variants.
5. **Struct-dominant mix with a unit catch-all.** Five struct variants
   plus one unit ``Unknown`` — the inverse of R89's four-unit-plus-one-
   struct ``HookEvent``.

Serde shape
-----------

:class:`SessionEvent`: ``#[serde(tag = "event_type", rename_all =
"snake_case")]`` — internally-tagged. Each struct variant serialises as
``{"event_type": "<snake_tag>", ...named fields...}``; :class:`Unknown`
serialises as ``{"event_type": "unknown"}``. Wire tags:

* :class:`TurnStarted` → ``"turn_started"`` (``turn_number``, ``model_id``,
  ``yolo_mode`` with ``#[serde(default)]``).
* :class:`TurnEnded` → ``"turn_ended"`` (``turn_number``, ``outcome``:
  :class:`~minimax_code.tool_protocol.turn_hook.TurnHookOutcome`,
  ``duration_ms``, ``tool_call_count``, ``model_id``).
* :class:`ToolCallStarted` → ``"tool_call_started"`` (``tool_call_id``,
  ``tool_name``, ``turn_number``).
* :class:`ToolCallCompleted` → ``"tool_call_completed"`` (``tool_call_id``,
  ``tool_name``, ``duration_ms``, ``outcome``: :class:`ToolCallOutcome`).
* :class:`PhaseChanged` → ``"phase_changed"`` (``phase``:
  :class:`SessionPhase`).
* :class:`Unknown` → ``"unknown"`` (the ``#[serde(other)]`` catch-all; any
  *unrecognized* ``event_type`` also deserialises here).

:class:`ToolCallOutcome` / :class:`SessionPhase`: ``#[serde(rename_all =
"snake_case")]`` ``#[derive(Copy)]`` string-enums (no tag — unit variants
serialise as bare strings) with an ``Unknown`` ``#[serde(other)]`` arm.

``to_wire`` lives on each variant dataclass;
:func:`session_event_from_wire` is the module-level dispatcher keyed on the
``event_type`` tag, with an :class:`Unknown` fallthrough for the
``#[serde(other)]`` catch-all.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from minimax_code.tool_protocol.turn_hook import TurnHookOutcome

__all__ = [
    "SessionEvent",
    "TurnStarted",
    "TurnEnded",
    "ToolCallStarted",
    "ToolCallCompleted",
    "PhaseChanged",
    "Unknown",
    "ToolCallOutcome",
    "SessionPhase",
    "session_event_from_wire",
]


@dataclass
class TurnStarted:
    """Turn began.

    Fields mirror ``turn_hook::BeforeTurnPayload`` but are structurally
    independent — this is a notification event, not a hook payload.
    :attr:`yolo_mode` carries the crate's first ``#[serde(default)]``.
    """

    turn_number: int
    model_id: str
    #: Whether yolo (auto-approve) mode was active for the turn. Wire-omitted
    #: → ``False`` (``#[serde(default)]``); always emitted on serialise.
    yolo_mode: bool = False

    def to_wire(self) -> dict[str, object]:
        return {
            "event_type": "turn_started",
            "turn_number": self.turn_number,
            "model_id": self.model_id,
            "yolo_mode": self.yolo_mode,
        }


@dataclass
class TurnEnded:
    """Turn ended.

    Fields mirror ``turn_hook::AfterTurnPayload`` but are structurally
    independent — this is a notification event, not a hook payload.
    :attr:`outcome` is a strict :class:`TurnHookOutcome` (unknown raises).
    """

    turn_number: int
    outcome: TurnHookOutcome
    duration_ms: int
    tool_call_count: int
    model_id: str

    def to_wire(self) -> dict[str, object]:
        return {
            "event_type": "turn_ended",
            "turn_number": self.turn_number,
            "outcome": str(self.outcome),
            "duration_ms": self.duration_ms,
            "tool_call_count": self.tool_call_count,
            "model_id": self.model_id,
        }


@dataclass
class ToolCallStarted:
    tool_call_id: str
    tool_name: str
    turn_number: int

    def to_wire(self) -> dict[str, object]:
        return {
            "event_type": "tool_call_started",
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "turn_number": self.turn_number,
        }


@dataclass
class ToolCallCompleted:
    """A completed tool call. :attr:`outcome` is a tolerant
    :class:`ToolCallOutcome` (unknown → ``UNKNOWN``)."""

    tool_call_id: str
    tool_name: str
    duration_ms: int
    outcome: ToolCallOutcome

    def to_wire(self) -> dict[str, object]:
        return {
            "event_type": "tool_call_completed",
            "tool_call_id": self.tool_call_id,
            "tool_name": self.tool_name,
            "duration_ms": self.duration_ms,
            "outcome": str(self.outcome),
        }


@dataclass
class PhaseChanged:
    """Session phase transitioned. :attr:`phase` is a tolerant
    :class:`SessionPhase` (unknown → ``UNKNOWN``)."""

    phase: SessionPhase

    def to_wire(self) -> dict[str, object]:
        return {"event_type": "phase_changed", "phase": str(self.phase)}


@dataclass
class Unknown:
    """Forward-compatibility catch-all.

    Older consumers that encounter a new ``event_type`` value deserialise it
    as :class:`Unknown` instead of failing. Consumers MUST silently ignore
    ``Unknown`` events. The original ``event_type`` value is not preserved;
    consumers that need to log unrecognized types should inspect the raw JSON
    before deserialising into :data:`SessionEvent`.
    """

    def to_wire(self) -> dict[str, object]:
        return {"event_type": "unknown"}


#: The session-event union — one of the six variants above.
SessionEvent = (
    TurnStarted
    | TurnEnded
    | ToolCallStarted
    | ToolCallCompleted
    | PhaseChanged
    | Unknown
)


class ToolCallOutcome(StrEnum):
    """Outcome of a completed tool call within a session event.

    ``#[serde(rename_all = "snake_case")]``, ``#[derive(Copy)]``,
    ``#[non_exhaustive]``. The :data:`UNKNOWN` variant is the
    ``#[serde(other)]`` forward-compat catch-all for outcomes added in newer
    protocol versions.
    """

    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"
    #: ``#[serde(other)]`` forward-compat catch-all.
    UNKNOWN = "unknown"

    @classmethod
    def from_wire(cls, value: str) -> ToolCallOutcome:
        """Reconstruct from its wire string.

        Unknown values → :data:`UNKNOWN` (the ``#[serde(other)]``
        forward-compat catch-all); never raises.
        """
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


class SessionPhase(StrEnum):
    """Current phase of the session lifecycle.

    ``#[serde(rename_all = "snake_case")]``, ``#[derive(Copy)]``,
    ``#[non_exhaustive]``. The :data:`UNKNOWN` variant is the
    ``#[serde(other)]`` catch-all for phases added in newer versions.
    """

    IDLE = "idle"
    SAMPLING = "sampling"
    TOOL_EXECUTION = "tool_execution"
    PERMISSION_PROMPT = "permission_prompt"
    #: ``#[serde(other)]`` forward-compat catch-all.
    UNKNOWN = "unknown"

    @classmethod
    def from_wire(cls, value: str) -> SessionPhase:
        """Reconstruct from its wire string.

        Unknown values → :data:`UNKNOWN` (the ``#[serde(other)]``
        forward-compat catch-all); never raises.
        """
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


def _turn_started_from_wire(data: dict[str, object]) -> TurnStarted:
    return TurnStarted(
        turn_number=int(data["turn_number"]),
        model_id=str(data["model_id"]),
        yolo_mode=bool(data.get("yolo_mode", False)),  # #[serde(default)]
    )


def _turn_ended_from_wire(data: dict[str, object]) -> TurnEnded:
    return TurnEnded(
        turn_number=int(data["turn_number"]),
        outcome=TurnHookOutcome.from_wire(str(data["outcome"])),  # strict
        duration_ms=int(data["duration_ms"]),
        tool_call_count=int(data["tool_call_count"]),
        model_id=str(data["model_id"]),
    )


def _tool_call_started_from_wire(data: dict[str, object]) -> ToolCallStarted:
    return ToolCallStarted(
        tool_call_id=str(data["tool_call_id"]),
        tool_name=str(data["tool_name"]),
        turn_number=int(data["turn_number"]),
    )


def _tool_call_completed_from_wire(data: dict[str, object]) -> ToolCallCompleted:
    return ToolCallCompleted(
        tool_call_id=str(data["tool_call_id"]),
        tool_name=str(data["tool_name"]),
        duration_ms=int(data["duration_ms"]),
        outcome=ToolCallOutcome.from_wire(str(data["outcome"])),  # tolerant
    )


def _phase_changed_from_wire(data: dict[str, object]) -> PhaseChanged:
    return PhaseChanged(phase=SessionPhase.from_wire(str(data["phase"])))  # tolerant


#: ``event_type`` wire tag → variant constructor. Does NOT include
#: ``"unknown"`` — that, plus any unrecognized tag, falls through to the
#: ``#[serde(other)]`` catch-all (:class:`Unknown`) in
#: :func:`session_event_from_wire`.
_EVENT_TYPE_HANDLERS: dict[str, Callable[[dict[str, object]], SessionEvent]] = {
    "turn_started": _turn_started_from_wire,
    "turn_ended": _turn_ended_from_wire,
    "tool_call_started": _tool_call_started_from_wire,
    "tool_call_completed": _tool_call_completed_from_wire,
    "phase_changed": _phase_changed_from_wire,
}


def session_event_from_wire(data: dict[str, object]) -> SessionEvent:
    """Reconstruct a :class:`SessionEvent` from its wire form.

    Dispatches on the ``event_type`` tag. Any unrecognized tag (including
    the literal ``"unknown"`` that :class:`Unknown` itself serialises to)
    deserialises as :class:`Unknown` — the ``#[serde(other)]`` forward-compat
    catch-all. Consumers MUST silently ignore :class:`Unknown` events.

    Nested enum fields are reconstructed via their own ``from_wire``:
    :attr:`TurnEnded.outcome` (strict :class:`TurnHookOutcome` — unknown
    raises), :attr:`ToolCallCompleted.outcome` (tolerant
    :class:`ToolCallOutcome` — unknown → ``UNKNOWN``), :attr:`PhaseChanged.phase`
    (tolerant :class:`SessionPhase` — unknown → ``UNKNOWN``).
    """
    tag = str(data["event_type"])
    handler = _EVENT_TYPE_HANDLERS.get(tag)
    if handler is None:
        # #[serde(other)] — forward-compat catch-all. Any unrecognized
        # event_type (and the literal "unknown") → Unknown.
        return Unknown()
    return handler(data)
