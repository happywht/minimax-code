"""Tests for ``minimax_code.resilience`` (R17 — circuit breaker).

Covers the three-state machine (CLOSED/OPEN/HALF_OPEN), the O(1) sliding
window, half-open probe lease reaping, the per-key registry, the retry
policy classifier, the fail-open ``guard()`` context manager, config env
loading, and the ``app.py`` registry singleton triplet.

A ``MockClock`` is injected into every breaker so transitions are
deterministic — no real-time ``time.sleep`` anywhere in this file.
"""

from __future__ import annotations

import pytest

from minimax_code.resilience import (
    BreakerConfig,
    BreakerOpen,
    BreakerState,
    CircuitBreaker,
    CircuitBreakerRegistry,
    Disposition,
    NoopObserver,
    Outcome,
    RetryPolicy,
    SlidingWindow,
    parse_failure_codes,
)
from minimax_code.resilience.window import MAX_WINDOW_ENTRIES


class MockClock:
    """Deterministic monotonic clock: ``t`` advances only via :meth:`advance`."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def hot_config(**overrides) -> BreakerConfig:
    """A config that trips easily: 4 failures at 100% error rate over a 60s window."""
    base = {
        "window_duration": 60.0,
        "min_samples": 4,
        "error_rate_threshold": 0.5,
        "open_duration": 10.0,
        "half_open_max_probes": 1,
        "failure_codes": frozenset({500}),
        "enabled": True,
    }
    base.update(overrides)
    return BreakerConfig(**base)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


def test_trips_after_threshold_breached():
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(), clock=clock)
    assert br.state is BreakerState.CLOSED
    for _ in range(4):
        clock.advance(1.0)
        br.record(Outcome.FAILURE)
    assert br.state is BreakerState.OPEN
    assert br.is_open is True


def test_does_not_trip_below_min_samples():
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(min_samples=5), clock=clock)
    for _ in range(4):
        clock.advance(1.0)
        br.record(Outcome.FAILURE)
    assert br.state is BreakerState.CLOSED


def test_does_not_trip_below_error_rate():
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(error_rate_threshold=0.6), clock=clock)
    for ok in (True, False, True, False):
        clock.advance(1.0)
        br.record(Outcome.SUCCESS if ok else Outcome.FAILURE)
    assert br.state is BreakerState.CLOSED
    assert abs(br.error_rate() - 0.5) < 1e-9


def test_open_to_half_open_after_cooldown():
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(open_duration=10.0), clock=clock)
    for _ in range(4):
        clock.advance(1.0)
        br.record(Outcome.FAILURE)
    assert br.state is BreakerState.OPEN
    clock.advance(10.0)  # exactly at the cool-down boundary
    assert br.state is BreakerState.HALF_OPEN


def test_open_sheds_via_check():
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(open_duration=10.0), clock=clock)
    for _ in range(4):
        clock.advance(1.0)
        br.record(Outcome.FAILURE)
    with pytest.raises(BreakerOpen) as ei:
        br.check()
    assert ei.value.retry_after > 0


def test_half_open_probe_success_closes_and_clears_window():
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(), clock=clock)
    for _ in range(4):
        clock.advance(1.0)
        br.record(Outcome.FAILURE)
    clock.advance(10.0)
    assert br.state is BreakerState.HALF_OPEN
    br.check()  # claim probe slot
    br.record(Outcome.SUCCESS)
    assert br.state is BreakerState.CLOSED
    assert br.sample_count() == 0  # window cleared on close


def test_half_open_probe_failure_reopens():
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(), clock=clock)
    for _ in range(4):
        clock.advance(1.0)
        br.record(Outcome.FAILURE)
    clock.advance(10.0)
    br.check()  # claim probe
    br.record(Outcome.FAILURE)
    assert br.state is BreakerState.OPEN


def test_half_open_probe_slot_exhausted():
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(half_open_max_probes=1), clock=clock)
    for _ in range(4):
        clock.advance(1.0)
        br.record(Outcome.FAILURE)
    clock.advance(10.0)
    br.check()  # claims the single slot
    with pytest.raises(BreakerOpen):
        br.check()  # no slot left, lease not yet expired


def test_half_open_abandoned_probe_lease_reaped():
    clock = MockClock()
    br = CircuitBreaker(
        "t", hot_config(open_duration=10.0, half_open_max_probes=1), clock=clock
    )
    for _ in range(4):
        clock.advance(1.0)
        br.record(Outcome.FAILURE)
    clock.advance(10.0)  # -> HALF_OPEN
    br.check()  # claim slot at t=14, never reports back
    clock.advance(10.0)  # t=24: lease (14+10) expired -> reaped
    br.check()  # succeeds by reaping the abandoned slot
    with pytest.raises(BreakerOpen):
        br.check()  # newly claimed slot, no report -> sheds


def test_disabled_breaker_never_sheds():
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(enabled=False), clock=clock)
    for _ in range(10):
        br.record(Outcome.FAILURE)
    br.check()  # would trip if enabled; must not raise
    assert br.state is BreakerState.CLOSED


# ---------------------------------------------------------------------------
# Sliding window
# ---------------------------------------------------------------------------


def test_window_incremental_error_rate():
    w = SlidingWindow()
    w.push(True, 1.0)
    w.push(False, 2.0)
    w.push(True, 3.0)
    assert w.sample_count() == 3
    assert abs(w.error_rate() - (2 / 3)) < 1e-9


def test_window_evict_old_samples():
    w = SlidingWindow()
    w.push(True, 0.0)
    w.push(True, 1.0)
    w.evict(window=0.5, now=1.0)  # cutoff=0.5 -> drop t=0.0
    assert w.sample_count() == 1
    assert w.error_rate() == 1.0


def test_window_max_entries_cap():
    w = SlidingWindow()
    for i in range(MAX_WINDOW_ENTRIES + 50):
        w.push(True, float(i))
    assert w.sample_count() == MAX_WINDOW_ENTRIES


def test_window_clear_resets_failure_count():
    w = SlidingWindow()
    w.push(True, 0.0)
    w.push(False, 1.0)
    w.clear()
    assert w.sample_count() == 0
    assert w.error_rate() == 0.0


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_same_key_same_instance():
    reg = CircuitBreakerRegistry(BreakerConfig.server())
    a = reg.get("llm:anthropic")
    b = reg.get("llm:anthropic")
    assert a is b


def test_registry_different_keys_different_instances():
    reg = CircuitBreakerRegistry(BreakerConfig.server())
    assert reg.get("a") is not reg.get("b")


def test_registry_disabled_returns_none():
    reg = CircuitBreakerRegistry(BreakerConfig(enabled=False))
    assert reg.get("x") is None
    assert reg.enabled is False


def test_registry_clear_drops_instances():
    reg = CircuitBreakerRegistry(BreakerConfig.server())
    reg.get("k")
    assert list(reg.keys()) == ["k"]
    reg.clear()
    assert list(reg.keys()) == []


# ---------------------------------------------------------------------------
# Retry policy
# ---------------------------------------------------------------------------


def test_retry_policy_server_preset():
    p = RetryPolicy.server()
    assert p.classify(200) is None
    assert p.classify(429) == Disposition.RETRYABLE
    assert p.classify(500) == Disposition.RETRYABLE
    assert p.classify(503) == Disposition.RETRYABLE
    assert p.classify(400) == Disposition.TERMINAL
    assert p.should_retry(429) is True
    assert p.should_retry(400) is False


def test_retry_policy_client_storage_preset():
    p = RetryPolicy.client_storage()
    assert p.classify(401) == Disposition.AUTH_REFRESH
    assert p.classify(404) == Disposition.TERMINAL
    assert p.classify(500) == Disposition.RETRYABLE  # 5xx always retryable
    assert p.classify(418) == Disposition.RETRYABLE  # falls to default


# ---------------------------------------------------------------------------
# guard() fail-open
# ---------------------------------------------------------------------------


def test_guard_records_success():
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(), clock=clock)
    with br.guard():
        pass
    assert br.sample_count() == 1
    assert br.error_rate() == 0.0


def test_guard_records_failure_and_reraises():
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(), clock=clock)
    with pytest.raises(ValueError, match="boom"):
        with br.guard():
            raise ValueError("boom")
    assert br.sample_count() == 1
    assert br.error_rate() == 1.0


def test_guard_propagates_breaker_open_without_entering_block():
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(), clock=clock)
    for _ in range(4):
        clock.advance(1.0)
        br.record(Outcome.FAILURE)
    entered = False
    with pytest.raises(BreakerOpen):
        with br.guard():
            entered = True
    assert entered is False


def test_guard_failopen_on_observer_fault():
    clock = MockClock()

    class BadObserver(NoopObserver):
        def on_outcome(self, outcome: Outcome) -> None:
            raise RuntimeError("observer boom")

    br = CircuitBreaker("t", hot_config(), clock=clock, observer=BadObserver())
    # record() calls the observer directly and would raise; guard() must
    # swallow that internal fault so the guarded block completes cleanly.
    with br.guard():
        pass


# ---------------------------------------------------------------------------
# Config + env loading
# ---------------------------------------------------------------------------


def test_config_server_preset_defaults():
    c = BreakerConfig.server()
    assert c.min_samples == 10
    assert c.error_rate_threshold == 0.5
    assert c.window_duration == 60.0
    assert c.open_duration == 10.0
    assert {429, 500, 502, 503, 504} <= c.failure_codes


def test_config_client_preset_defaults():
    c = BreakerConfig.client()
    assert c.min_samples == 5
    assert c.open_duration == 60.0
    assert 401 in c.failure_codes


def test_config_from_env_parses(monkeypatch):
    monkeypatch.setenv("MINIMAX_CODE_CB_MIN_SAMPLES", "7")
    monkeypatch.setenv("MINIMAX_CODE_CB_OPEN_DURATION_SECS", "30")
    monkeypatch.setenv("MINIMAX_CODE_CB_FAILURE_CODES", "500,503,999")
    monkeypatch.setenv("MINIMAX_CODE_CB_ENABLED", "0")
    c = BreakerConfig.from_env()
    assert c.min_samples == 7
    assert c.open_duration == 30.0
    assert c.failure_codes == frozenset({500, 503, 999})
    assert c.enabled is False


def test_config_from_env_bad_value_falls_back(monkeypatch):
    monkeypatch.setenv("MINIMAX_CODE_CB_MIN_SAMPLES", "not-a-number")
    c = BreakerConfig.from_env()
    assert c.min_samples == 10  # default preserved


def test_parse_failure_codes_drops_invalid():
    assert parse_failure_codes("500,abc,503") == frozenset({500, 503})
    assert parse_failure_codes("") == frozenset()


def test_config_is_failure_status():
    c = BreakerConfig.server()
    assert c.is_failure_status(500) is True
    assert c.is_failure_status(200) is False


# ---------------------------------------------------------------------------
# app.py singleton triplet
# ---------------------------------------------------------------------------


def test_app_breaker_registry_set_get():
    import minimax_code.app as app

    reg = CircuitBreakerRegistry(BreakerConfig.server())
    try:
        app.set_breaker_registry(reg)
        assert app.get_breaker_registry() is reg
    finally:
        app.set_breaker_registry(None)


def test_app_breaker_registry_disabled_by_env(monkeypatch):
    import minimax_code.app as app

    app.set_breaker_registry(None)
    monkeypatch.setenv("MINIMAX_CODE_BREAKER", "0")
    assert app.ensure_breaker_registry() is None


def test_app_breaker_registry_lazy_builds_once(monkeypatch):
    import minimax_code.app as app

    app.set_breaker_registry(None)
    monkeypatch.delenv("MINIMAX_CODE_BREAKER", raising=False)
    reg = app.ensure_breaker_registry()
    try:
        assert reg is not None
        assert app.ensure_breaker_registry() is reg  # cached
        assert reg.get("k") is reg.get("k")  # registry itself lazy-creates
    finally:
        app.set_breaker_registry(None)


# ---------------------------------------------------------------------------
# Observer dispatch (R21) — fail-open, symmetric with the reliability stack
# ---------------------------------------------------------------------------


class _RecordingObserver(NoopObserver):
    """Captures every transition + outcome for assertion (R21)."""

    def __init__(self) -> None:
        self.transitions: list[tuple[BreakerState, BreakerState, str]] = []
        self.outcomes: list[Outcome] = []

    def on_state_change(
        self, old: BreakerState, new: BreakerState, reason: str
    ) -> None:
        self.transitions.append((old, new, reason))

    def on_outcome(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)


def test_observer_fires_on_trip_and_outcome() -> None:
    """R21: a trip emits on_state_change CLOSED→OPEN and on_outcome per record."""

    obs = _RecordingObserver()
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(min_samples=1), clock=clock, observer=obs)
    br.record(Outcome.FAILURE)
    assert obs.outcomes == [Outcome.FAILURE]
    assert len(obs.transitions) == 1
    old, new, _reason = obs.transitions[0]
    assert (old, new) == (BreakerState.CLOSED, BreakerState.OPEN)


def test_observer_fires_on_half_open_recovery() -> None:
    """R21: HALF_OPEN→CLOSED on a successful probe fires on_state_change."""

    obs = _RecordingObserver()
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(min_samples=1), clock=clock, observer=obs)
    br.record(Outcome.FAILURE)  # trip
    obs.transitions.clear()
    clock.advance(br.config.open_duration)
    br.check()  # → HALF_OPEN
    br.record(Outcome.SUCCESS)
    assert (obs.transitions[-1][0], obs.transitions[-1][1]) == (
        BreakerState.HALF_OPEN,
        BreakerState.CLOSED,
    )


def test_attach_observer_swaps_active() -> None:
    """attach_observer replaces the active observer; later events route to it only."""

    obs1 = _RecordingObserver()
    clock = MockClock()
    br = CircuitBreaker("t", hot_config(min_samples=1), clock=clock, observer=obs1)
    obs2 = _RecordingObserver()
    br.attach_observer(obs2)
    br.record(Outcome.FAILURE)
    assert obs1.outcomes == []  # detached
    assert obs2.outcomes == [Outcome.FAILURE]  # active


def test_observer_fault_is_fail_open() -> None:
    """R21: a buggy observer raising on every hook must NOT corrupt the breaker.

    The ``_notify_*`` wrappers swallow observer faults, so the state machine
    advances normally even when on_state_change / on_outcome explode.
    """

    class _Boom(NoopObserver):
        def on_state_change(self, old, new, reason):  # noqa: ANN001
            raise RuntimeError("boom")

        def on_outcome(self, outcome):  # noqa: ANN001
            raise RuntimeError("boom")

    clock = MockClock()
    br = CircuitBreaker("t", hot_config(min_samples=1), clock=clock, observer=_Boom())
    br.record(Outcome.FAILURE)  # trip path notifies + records outcome
    assert br.state is BreakerState.OPEN  # state machine intact despite fault


# ---------------------------------------------------------------------------
# Registry observer factory (R21)
# ---------------------------------------------------------------------------


def test_registry_factory_attaches_to_new_breakers() -> None:
    """Factory-installed observer rides every breaker the registry lazily creates."""

    reg = CircuitBreakerRegistry(hot_config(min_samples=1))
    observers: dict[str, _RecordingObserver] = {}

    def factory(key: str) -> _RecordingObserver:
        observers[key] = _RecordingObserver()
        return observers[key]

    reg.attach_observer_factory(factory)
    br = reg.get("llm:anthropic")
    assert br is not None
    br.record(Outcome.FAILURE)
    assert "llm:anthropic" in observers
    assert observers["llm:anthropic"].outcomes == [Outcome.FAILURE]


def test_registry_factory_retrofits_existing_breakers() -> None:
    """attach_observer_factory rewires breakers that already exist, immediately."""

    reg = CircuitBreakerRegistry(hot_config(min_samples=1))
    reg.get("k")  # materialise BEFORE the factory is set
    observers: dict[str, _RecordingObserver] = {}

    def factory(key: str) -> _RecordingObserver:
        observers[key] = _RecordingObserver()
        return observers[key]

    reg.attach_observer_factory(factory)
    assert "k" in observers  # retrofit fired for the pre-existing breaker
    br = reg.get("k")
    assert br is not None
    br.record(Outcome.FAILURE)
    assert observers["k"].outcomes == [Outcome.FAILURE]  # wired in place


def test_registry_factory_none_is_noop() -> None:
    """Passing None clears the factory; later breakers stay unobserved."""

    reg = CircuitBreakerRegistry(hot_config(min_samples=1))
    reg.attach_observer_factory(lambda key: _RecordingObserver())
    reg.attach_observer_factory(None)  # unset
    # A fresh key post-clear must not trip the factory — verified by asserting
    # no exception and the registry still serves a working breaker.
    br = reg.get("fresh")
    assert br is not None
    br.record(Outcome.FAILURE)  # no observer attached; breaker works bare
