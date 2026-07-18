"""Tests for the R20 reliability→telemetry observer adapter.

``ReliabilityTelemetryObserver`` translates breaker state transitions into
``CIRCUIT_BREAKER`` telemetry events on a real ``TelemetryEngine``. This
file pins three contracts that downstream dashboards depend on:

* the event shape — type / severity / endpoint-scoped ``session_id=None``
  / payload carrying the stable reason string;
* the severity policy — OPEN (trip / probe_failure) → ERROR, recovery
  transitions (open_elapsed / probe_success) → INFO;
* fail-open / zero-overhead behaviour when no engine is attached.
"""

from __future__ import annotations

from minimax_code.agent.reliability import BreakerState, Outcome
from minimax_code.telemetry import EventType, Severity, TelemetryEngine
from minimax_code.telemetry.observer_adapter import ReliabilityTelemetryObserver


def test_observer_emits_error_event_on_trip() -> None:
    """CLOSED→OPEN is a degraded state an operator must see → ERROR."""

    engine = TelemetryEngine()
    obs = ReliabilityTelemetryObserver(lambda: engine, name="llm")
    obs.on_state_change(BreakerState.CLOSED, BreakerState.OPEN, "trip")
    cbs = engine.recent(event_type=EventType.CIRCUIT_BREAKER)
    assert len(cbs) == 1
    ev = cbs[0]
    assert ev["severity"] == Severity.ERROR
    assert ev["session_id"] is None  # endpoint-scoped, not turn-scoped
    assert ev["name"] == "llm"
    assert ev["payload"] == {
        "breaker": "llm",
        "old": "closed",
        "new": "open",
        "reason": "trip",
    }


def test_observer_emits_info_events_on_recovery() -> None:
    """Recovery transitions (cool-down elapsed, probe success) are good news
    → INFO, never ERROR. A tripped-then-recovered breaker must not leave a
    lingering ERROR-severity trail in the event stream."""

    engine = TelemetryEngine()
    obs = ReliabilityTelemetryObserver(lambda: engine, name="llm")
    obs.on_state_change(BreakerState.OPEN, BreakerState.HALF_OPEN, "open_elapsed")
    obs.on_state_change(BreakerState.HALF_OPEN, BreakerState.CLOSED, "probe_success")
    cbs = engine.recent(event_type=EventType.CIRCUIT_BREAKER)
    assert [e["severity"] for e in cbs] == [Severity.INFO, Severity.INFO]
    assert [e["payload"]["reason"] for e in cbs] == [
        "open_elapsed",
        "probe_success",
    ]


def test_observer_probe_failure_is_error() -> None:
    """A HALF_OPEN probe failing re-OPENS the breaker — back to ERROR."""

    engine = TelemetryEngine()
    obs = ReliabilityTelemetryObserver(lambda: engine, name="llm")
    obs.on_state_change(BreakerState.HALF_OPEN, BreakerState.OPEN, "probe_failure")
    cbs = engine.recent(event_type=EventType.CIRCUIT_BREAKER)
    assert cbs[0]["severity"] == Severity.ERROR
    assert cbs[0]["payload"]["reason"] == "probe_failure"


def test_observer_no_emit_when_engine_none() -> None:
    """Zero-overhead when telemetry is disabled: no allocation, no raise.
    The getter returns None and the observer short-circuits silently — this
    is the path every breaker takes until app.py injects the engine."""

    obs = ReliabilityTelemetryObserver(lambda: None, name="llm")
    obs.on_state_change(BreakerState.CLOSED, BreakerState.OPEN, "trip")  # no raise
    obs.on_outcome(Outcome.FAILURE)  # no raise


def test_observer_on_outcome_is_noop() -> None:
    """``on_outcome`` is intentionally a no-op — outcomes are high-frequency
    (one per call) and low-signal; the metrics layer suits those better than
    the event stream, which would drown in one event per LLM attempt."""

    engine = TelemetryEngine()
    obs = ReliabilityTelemetryObserver(lambda: engine, name="llm")
    obs.on_outcome(Outcome.FAILURE)
    obs.on_outcome(Outcome.SUCCESS)
    assert engine.recent(event_type=EventType.CIRCUIT_BREAKER) == []


def test_observer_distinguishes_breakers_by_name() -> None:
    """Multiple breakers share one engine; the ``name`` + ``payload.breaker``
    fields keep them apart in the event stream (llm vs tool:search)."""

    engine = TelemetryEngine()
    obs_llm = ReliabilityTelemetryObserver(lambda: engine, name="llm")
    obs_tool = ReliabilityTelemetryObserver(lambda: engine, name="tool:search")
    obs_llm.on_state_change(BreakerState.CLOSED, BreakerState.OPEN, "trip")
    obs_tool.on_state_change(BreakerState.CLOSED, BreakerState.OPEN, "trip")
    cbs = engine.recent(event_type=EventType.CIRCUIT_BREAKER)
    assert {e["name"] for e in cbs} == {"llm", "tool:search"}
    assert {e["payload"]["breaker"] for e in cbs} == {"llm", "tool:search"}


def test_observer_handles_cross_stack_breaker_state() -> None:
    """R21: the adapter uses ``==`` (StrEnum value compare), so an OPEN transition
    from the resilience stack's separate BreakerState class still maps to ERROR.

    ``resilience.BreakerState.OPEN is reliability.BreakerState.OPEN`` is False
    (different classes), but StrEnum ``==`` compares the underlying str — so the
    severity branch fires regardless of which stack raised the transition.
    """

    from minimax_code.resilience import BreakerState as ResilienceBreakerState

    engine = TelemetryEngine()
    obs = ReliabilityTelemetryObserver(lambda: engine, name="llm:transport")
    obs.on_state_change(
        ResilienceBreakerState.CLOSED, ResilienceBreakerState.OPEN, "error-rate"
    )
    cbs = engine.recent(event_type=EventType.CIRCUIT_BREAKER)
    assert len(cbs) == 1
    assert cbs[0]["severity"] == Severity.ERROR
    assert cbs[0]["payload"]["new"] == "open"


def test_resilience_breaker_feeds_telemetry_via_registry_factory() -> None:
    """R21 end-to-end: a resilience-stack breaker tripping through the registry
    observer factory emits a CIRCUIT_BREAKER event on the shared engine.

    Proves the two-stack bridge: the same ``ReliabilityTelemetryObserver`` class
    wired via the resilience registry feeds one event stream, distinguishable
    from the reliability breaker by ``name`` (``llm:anthropic`` vs ``llm``)."""

    from minimax_code.resilience import (
        BreakerConfig as ResilienceBreakerConfig,
    )
    from minimax_code.resilience import (
        CircuitBreakerRegistry,
        Outcome,
    )

    engine = TelemetryEngine()
    reg = CircuitBreakerRegistry(
        ResilienceBreakerConfig(min_samples=1, open_duration=10.0)
    )
    reg.attach_observer_factory(
        lambda key: ReliabilityTelemetryObserver(lambda: engine, name=key)
    )
    br = reg.get("llm:anthropic")
    assert br is not None
    br.record(Outcome.FAILURE)  # trips → OPEN

    cbs = engine.recent(event_type=EventType.CIRCUIT_BREAKER)
    assert len(cbs) == 1
    ev = cbs[0]
    assert ev["severity"] == Severity.ERROR  # OPEN via cross-stack == compare
    assert ev["payload"]["breaker"] == "llm:anthropic"
    assert ev["payload"]["new"] == "open"
