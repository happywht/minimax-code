"""Lifecycle contributor ABCs (R25).

Ports the trait half of grok-build's ``xai-agent-lifecycle`` crate.
Four contributor families, each a no-op default so registration is
opt-in per hook — a contributor that only cares about ``on_turn_done``
subclasses :class:`TurnLifecycleContributor` and overrides that one
method, inheriting empty defaults for the other three.

Python simplification
---------------------
grok ships two variants of every trait (a thread-local and a
``'static`` clone-friendly one) because Rust's borrow checker forces
the split. Python's GIL + reference semantics make the second variant
redundant — one ABC per family is the faithful port (the ``Sync +
Clone`` bound grok needs for its registry is free in Python).

Dispatch contract
-----------------
The host calls these in registration order, fire-and-forget. A raising
contributor is logged and skipped — never propagated — so one bad
extension cannot kill a turn. That fail-open stance lives in the host
(:meth:`AgentCore._fire`), not here; these ABCs only shape the surface.
"""

# ruff: noqa: B024, B027 — trait-style ABCs. Every hook carries a default
# body so a subclass overrides only the boundaries it cares about (faithful
# grok ``xai-agent-lifecycle`` port — Rust traits give every method a
# default the same way). B024/B027 flag exactly that pattern; the warning
# would force ``@abstractmethod`` and break the opt-in-per-hook contract.

from __future__ import annotations

from abc import ABC

from .types import (
    CommandAction,
    CommandInvocation,
    CommandSpec,
    SessionIdleInput,
    TurnAbortInput,
    TurnDoneInput,
    TurnErrorInput,
    TurnInputContext,
    TurnInputFragment,
    TurnStartInput,
)

__all__ = [
    "TurnLifecycleContributor",
    "SessionLifecycleContributor",
    "TurnInputContributor",
    "CommandContributor",
]


class TurnLifecycleContributor(ABC):
    """Observe the four turn boundaries.

    Every method has an empty default, so a subclass overrides only the
    boundaries it cares about. The host dispatches them in registration
    order; one raising contributor never blocks the rest.
    """

    async def on_turn_start(self, inp: TurnStartInput) -> None:
        """Fired once before the first LLM call of the turn."""

    async def on_turn_done(self, inp: TurnDoneInput) -> None:
        """Fired when the turn produced a final answer."""

    async def on_turn_abort(self, inp: TurnAbortInput) -> None:
        """Fired when the turn was stopped (limit hit, cancelled, …)."""

    async def on_turn_error(self, inp: TurnErrorInput) -> None:
        """Fired when the turn raised an exception."""


class SessionLifecycleContributor(ABC):
    """Observe session-level idle transitions."""

    async def on_session_idle(self, inp: SessionIdleInput) -> None:
        """Fired when a session has no turn in flight."""


class TurnInputContributor(ABC):
    """Inject text fragments at turn start.

    The host concatenates every contributor's fragments (registration
    order) and prepends them to the user message. Pure data in; no
    control over the loop.
    """

    async def contribute_turn_input(
        self, ctx: TurnInputContext
    ) -> list[TurnInputFragment]:
        """Return fragments to prepend (empty list = contribute nothing)."""
        return []


class CommandContributor(ABC):
    """Own a set of slash-style commands.

    Advertised commands appear in help; the host routes a parsed
    invocation to the first contributor (registration order) that owns
    the name. ``handle_command`` returns either a model-text rewrite or
    a plain "acted" signal.
    """

    def advertised_commands(self) -> list[CommandSpec]:
        """Names + help surface this contributor owns."""
        return []

    async def handle_command(self, inv: CommandInvocation) -> CommandAction:
        """Act on ``inv``.

        Default raises — a contributor that advertises a command must
        override this so the host's route table is never left dangling.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not handle {inv.name!r}"
        )
