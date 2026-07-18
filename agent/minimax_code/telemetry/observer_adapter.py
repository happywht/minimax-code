"""Telemetry adapter for the reliability-stack circuit breaker (R20).

Bridges :class:`minimax_code.agent.reliability.Observer` to
:class:`minimax_code.telemetry.engine.TelemetryEngine`. A breaker state
transition becomes one ``CIRCUIT_BREAKER`` telemetry event; per-outcome hooks
are intentionally no-op (high-frequency, low-signal — the metrics layer suits
those better than the event stream, which would drown in one event per call).

The engine is resolved through an injectable ``engine_getter`` callable rather
than a held reference: :class:`AgentCore`'s ``telemetry_engine`` is injected
*after* construction (from ``app.py``), so the observer must read it lazily at
emit time. This is the Python analogue of grok-build's ambient ``TelemetryCtx``
— without the ``tokio::task_local`` ceremony, a closure over the agent is
enough, and it sidesteps the late-injection ordering problem entirely.

Fail-open by construction: a missing engine, a disabled engine, or any emit
fault never propagates back into the breaker state machine (which itself wraps
observer calls in its own fail-open guard — belt and braces).

Scope decision
--------------
``CIRCUIT_BREAKER`` events are emitted with ``session_id=None`` because breaker
state is endpoint-scoped — the single ``llm`` breaker tripping affects every
session, so attributing the transition to whichever session happened to trigger
it would mislead dashboards. Per-session attribution would require grok's
ambient context (``contextvars``) plumbed through the call stack; deferred until
a real per-session breaker exists (YAGNI today — there is one shared ``llm``
breaker per process).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from ..agent.reliability import BreakerState, Observer, Outcome
from .events import EventType, Severity, TelemetryEvent

logger = logging.getLogger(__name__)


class ReliabilityTelemetryObserver(Observer):
    """Emit ``CIRCUIT_BREAKER`` events on breaker state transitions.

    Parameters
    ----------
    engine_getter:
        Zero-arg callable returning the live :class:`TelemetryEngine` or
        ``None``. Resolved at emit time so the observer tolerates the engine
        being injected after the breaker is constructed.
    name:
        Breaker key (e.g. ``"llm"``) — carried as the event ``name`` and in
        ``payload.breaker`` so multiple breakers are distinguishable in the
        event stream.
    """

    def __init__(
        self,
        engine_getter: Callable[[], Any],
        *,
        name: str,
    ) -> None:
        self._engine_getter = engine_getter
        self._name = name

    def on_state_change(
        self, old: BreakerState, new: BreakerState, reason: str
    ) -> None:
        engine = self._engine_getter()
        if engine is None:
            return  # telemetry disabled — zero overhead, no allocation
        # An OPEN breaker is a degraded state an operator must see; recovery
        # transitions (HALF_OPEN, CLOSED) are informational good news.
        severity = Severity.ERROR if new is BreakerState.OPEN else Severity.INFO
        engine.emit(
            TelemetryEvent(
                type=EventType.CIRCUIT_BREAKER,
                session_id=None,  # endpoint-scoped, not turn-scoped
                severity=severity,
                name=self._name,
                payload={
                    "breaker": self._name,
                    "old": old.value,
                    "new": new.value,
                    "reason": reason,
                },
            )
        )

    def on_outcome(self, outcome: Outcome) -> None:  # noqa: D401 - short
        """No-op — outcomes are high-frequency; metrics layers handle those."""
        return None


__all__ = ["ReliabilityTelemetryObserver"]
