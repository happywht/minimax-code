"""Lifecycle contributor data types (R25).

Ports the value-object half of grok-build's ``xai-agent-lifecycle``
crate — the pure-data inputs every contributor receives and the
spec/action types the command + turn-input contributors exchange with
the host. Frozen dataclasses, no behaviour: the host owns the run
loop, contributors only read these payloads.

The four families
-----------------
* **Turn lifecycle** — :class:`TurnStartInput` / :class:`TurnDoneInput` /
  :class:`TurnAbortInput` / :class:`TurnErrorInput` bracket one
  conversation turn. Exactly one terminal event (done / abort / error)
  fires per turn.
* **Session lifecycle** — :class:`SessionIdleInput` signals a session
  with no turn in flight.
* **Turn input** — :class:`TurnInputContext` + :class:`TurnInputFragment`
  let a contributor prepend text to the turn's user message.
* **Command** — :class:`CommandSpec` (advertised surface) /
  :class:`CommandInvocation` (a parsed call) / :data:`CommandAction`
  (rewrite or acted).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

__all__ = [
    "TurnAbortReason",
    "TurnStartInput",
    "TurnDoneInput",
    "TurnAbortInput",
    "TurnErrorInput",
    "SessionIdleInput",
    "TurnInputContext",
    "TurnInputFragment",
    "CommandSpec",
    "CommandInvocation",
    "CommandRewrite",
    "CommandActed",
    "CommandAction",
]


class TurnAbortReason(StrEnum):
    """Why a turn ended without producing a final answer.

    Mirrors grok's two-variant enum, plus ``MAX_ITERATIONS`` for the
    iteration-budget exhaustion path (v1.1.0). MiniMax never raises a
    transport disconnect inside a turn today; ``DISCONNECTED`` is kept
    for parity so a future remote-agent transport can distinguish it.
    ``MAX_ITERATIONS`` separates budget truncation from user cancel —
    both previously funnelled into ``INTERRUPTED``, leaving downstream
    observers unable to tell a capped loop from a stop button.
    """

    DISCONNECTED = "disconnected"
    INTERRUPTED = "interrupted"
    MAX_ITERATIONS = "max_iterations"


# -- turn lifecycle ---------------------------------------------------------


@dataclass(frozen=True)
class TurnStartInput:
    """Payload for ``on_turn_start``.

    ``synthetic`` marks turns the host launches on the model's behalf
    (e.g. a command rewrite) rather than a real user message.
    """

    synthetic: bool = False


@dataclass(frozen=True)
class TurnDoneInput:
    """Payload for ``on_turn_done`` — the turn produced a final answer."""

    iterations: int = 0


@dataclass(frozen=True)
class TurnAbortInput:
    """Payload for ``on_turn_abort`` — the turn was stopped mid-flight."""

    reason: TurnAbortReason


@dataclass(frozen=True)
class TurnErrorInput:
    """Payload for ``on_turn_error`` — the turn raised an exception."""

    message: str


# -- session lifecycle ------------------------------------------------------


@dataclass(frozen=True)
class SessionIdleInput:
    """Payload for ``on_session_idle`` — no turn is in flight."""


# -- turn input contributor -------------------------------------------------


@dataclass(frozen=True)
class TurnInputContext:
    """Identifies the turn a ``contribute_turn_input`` call belongs to."""

    turn_id: str
    synthetic: bool = False


@dataclass(frozen=True)
class TurnInputFragment:
    """A text fragment a contributor wants prepended to the turn input."""

    text: str


# -- command contributor ----------------------------------------------------


@dataclass(frozen=True)
class CommandSpec:
    """One advertised slash-style command's surface.

    ``arg_hint`` is a free-form hint for help text — grok keeps it loose
    rather than a rigid parser grammar.
    """

    name: str
    description: str
    arg_hint: str = ""


@dataclass(frozen=True)
class CommandInvocation:
    """An actual command the host parsed and routed to its contributor."""

    name: str
    args: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class CommandRewrite:
    """``handle_command`` outcome: replace the model's text mid-turn."""

    model_text: str


@dataclass(frozen=True)
class CommandActed:
    """``handle_command`` outcome: side-effecting work, no text rewrite.

    The turn continues unaffected; ``note`` is an optional human hint.
    """

    note: str = ""


#: The two ways a ``handle_command`` call can resolve. A
#: :class:`CommandRewrite` hands new text back to the host to feed the
#: model; a :class:`CommandActed` means the contributor already did its
#: work and the turn proceeds.
CommandAction = CommandRewrite | CommandActed
