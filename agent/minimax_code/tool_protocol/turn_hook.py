"""Turn-hook primitives — first leaf landed (R90).

Fusion of grok-build's ``xai-tool-protocol::turn_hook`` — the
sampler↔workspace turn-hook protocol. The module is large (700 lines,
``pub mod turn_hook`` with **no** ``pub use`` re-export in lib.rs, so none
of its symbols travel the barrel); R90 lands only the minimal leaf that
:mod:`session_event` depends on — :class:`TurnHookOutcome` and the
:data:`TURN_HOOK_KIND` constant — with the remainder (``TurnHookRequest`` /
``BeforeTurnPayload`` / ``AfterTurnPayload`` / ``InjectionRole`` / ...)
deferred to later rounds.

Mirrors the Rust ``TurnHookOutcome`` enum: ``#[serde(rename_all =
"snake_case")]`` with **no** ``#[serde(other)]`` arm — unknown values are
**strictly rejected** (``serde_json::from_value::<TurnHookOutcome>("timeout")
.is_err()`` in the crate's own tests). This is the strict counterpart to
the tolerant :class:`~minimax_code.tool_protocol.session_event.ToolCallOutcome`
/ :class:`~minimax_code.tool_protocol.session_event.SessionPhase` (both R90,
both with an ``Unknown`` ``#[serde(other)]`` catch-all) — R90 is the crate's
strict↔tolerant turning point. Named ``TurnHookOutcome`` (not
``TurnOutcome``) to avoid colliding with the shell's existing ``TurnOutcome``
and the telemetry crate's ``TurnOutcomeLabel``; module-qualified usage
(``turn_hook::TurnHookOutcome``) is still recommended.

Serde shape
-----------

``#[serde(rename_all = "snake_case")]`` — unit variants serialise as bare
strings (no tag). Wire values:

* :data:`TurnHookOutcome.COMPLETED` → ``"completed"``.
* :data:`TurnHookOutcome.CANCELLED` → ``"cancelled"``.
* :data:`TurnHookOutcome.ERROR` → ``"error"``.

``#[derive(Copy)]`` — value-equal, hashable, comparable (mirrored via
:class:`enum.StrEnum`). :meth:`TurnHookOutcome.from_wire` raises
:class:`ValueError` on unknown values (strict — no catch-all).
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["TurnHookOutcome", "TURN_HOOK_KIND"]

#: ``HookEvent::Custom`` kind for the request/response turn hook
#: (mirrors ``pub const TURN_HOOK_KIND: &str = "turn_hook";``).
TURN_HOOK_KIND: str = "turn_hook"


class TurnHookOutcome(StrEnum):
    """Turn outcome as observed by the sampler.

    ``#[serde(rename_all = "snake_case")]``, ``#[derive(Copy)]``,
    ``#[non_exhaustive]``. Strict — **no** ``#[serde(other)]`` arm, so an
    unknown wire value raises :class:`ValueError` (contrast
    :class:`~minimax_code.tool_protocol.session_event.ToolCallOutcome`,
    which deserialises unknown values as ``UNKNOWN``).
    """

    #: Turn completed normally (model finished generating).
    COMPLETED = "completed"
    #: Turn was cancelled by the user (Ctrl+C / abort).
    CANCELLED = "cancelled"
    #: Turn ended due to an error.
    ERROR = "error"

    @classmethod
    def from_wire(cls, value: str) -> TurnHookOutcome:
        """Reconstruct a :class:`TurnHookOutcome` from its wire string.

        Strict — no ``#[serde(other)]`` catch-all. Unknown values raise
        :class:`ValueError` (mirrors
        ``serde_json::from_value::<TurnHookOutcome>("timeout").is_err()``).
        """
        # raises ValueError on unknown — strict, no catch-all
        return cls(value)
