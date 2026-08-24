"""Tests for the R13 reliability layer — circuit breaker + retry.

Every public symbol in ``minimax_code.agent.reliability`` is covered:

- ``BreakerConfig`` presets + ``from_env``
- ``CircuitBreaker`` state machine (CLOSED/OPEN/HALF_OPEN), pruning,
  status-code coercion, disabled short-circuit
- ``CircuitBreakerRegistry`` key isolation
- ``classify_exception`` disposition matrix
- ``with_retry`` success / exhaustion / terminal fast-fail / on_retry /
  injectable sleep / jitter bounds

Time-dependent transitions use a tiny ``open_duration`` + a real
``asyncio.sleep`` (the breaker reads ``time.monotonic()``, which is not
injectable); retry tests inject a recording sleep so they never block.
"""

from __future__ import annotations

import asyncio

import pytest

from minimax_code.agent.reliability import (
    BreakerConfig,
    BreakerOpen,
    BreakerState,
    CircuitBreaker,
    CircuitBreakerRegistry,
    Observer,
    Outcome,
    RetryPolicy,
    classify_exception,
    with_retry,
)
from minimax_code.agent.reliability.retry import Disposition, _delay_for
from minimax_code.agent.types import LLMError, LLMStreamTimeout

# ---------------------------------------------------------------------------
# BreakerConfig
# ---------------------------------------------------------------------------


def test_breaker_config_server_preset():
    s = BreakerConfig.server()
    assert s.open_duration == 30.0
    assert s.min_samples == 5
    assert s.error_rate_threshold == 0.5
    assert 429 in s.failure_codes and 503 in s.failure_codes


def test_breaker_config_client_preset():
    c = BreakerConfig.client()
    assert c.min_samples == 3
    assert c.open_duration == 60.0


def test_breaker_config_from_env(monkeypatch):
    monkeypatch.setenv("TESTCB_OPEN_DURATION", "7.5")
    monkeypatch.setenv("TESTCB_MIN_SAMPLES", "9")
    monkeypatch.setenv("TESTCB_ENABLED", "0")
    cfg = BreakerConfig.from_env(prefix="TESTCB")
    assert cfg.open_duration == 7.5
    assert cfg.min_samples == 9
    assert cfg.enabled is False


def test_breaker_config_from_env_defaults(monkeypatch):
    # Wipe any stray TESTCB_* to assert the documented defaults win.
    for k in ("TESTCB_WINDOW", "TESTCB_OPEN_DURATION", "TESTCB_ENABLED"):
        monkeypatch.delenv(k, raising=False)
    cfg = BreakerConfig.from_env(prefix="TESTCB")
    assert cfg.window_duration == 60.0
    assert cfg.enabled is True


# ---------------------------------------------------------------------------
# CircuitBreaker — state machine
# ---------------------------------------------------------------------------


def _cfg(**over) -> BreakerConfig:
    """BreakerConfig with test-friendly defaults (overridable per-case)."""

    base = {
        "window_duration": 100.0,
        "min_samples": 3,
        "error_rate_threshold": 0.5,
        "open_duration": 100.0,
        "half_open_max_probes": 1,
    }
    base.update(over)
    return BreakerConfig(**base)


async def test_breaker_starts_closed_and_admits():
    cb = CircuitBreaker(_cfg())
    assert cb.state is BreakerState.CLOSED
    await cb.check()  # must not raise


async def test_breaker_trips_only_at_threshold():
    cb = CircuitBreaker(_cfg(min_samples=3))
    await cb.record(Outcome.FAILURE)
    await cb.record(Outcome.FAILURE)
    assert cb.state is BreakerState.CLOSED  # 2 samples < min_samples
    assert cb.error_rate() == 1.0
    await cb.record(Outcome.FAILURE)
    assert cb.state is BreakerState.OPEN  # 3 samples, rate 1.0 >= 0.5


async def test_breaker_does_not_trip_below_error_rate():
    # The breaker evaluates incrementally on every record(), not just at
    # the final sample count. A failure-heavy prefix (e.g. 2/3 = 0.67)
    # trips even if the steady-state rate would settle lower. So this
    # mix keeps every prefix under threshold 0.9 once len >= min_samples.
    cb = CircuitBreaker(_cfg(min_samples=3, error_rate_threshold=0.9))
    for outcome in (
        Outcome.FAILURE, Outcome.SUCCESS, Outcome.FAILURE,
        Outcome.SUCCESS, Outcome.FAILURE, Outcome.SUCCESS,
    ):
        await cb.record(outcome)
    # Final rate 3/6 = 0.5; no prefix reached 0.9 → stays closed.
    assert cb.state is BreakerState.CLOSED
    assert cb.error_rate() == 0.5


async def test_open_vetoes_check():
    cb = CircuitBreaker(_cfg(min_samples=1))
    await cb.record(Outcome.FAILURE)
    assert cb.state is BreakerState.OPEN
    with pytest.raises(BreakerOpen) as ei:
        await cb.check()
    assert ei.value.state is BreakerState.OPEN
    # retry_after is the remaining cool-down, bounded by open_duration.
    assert 0 < ei.value.retry_after <= cb.config.open_duration


async def test_open_to_half_open_after_cooldown():
    cb = CircuitBreaker(_cfg(min_samples=1, open_duration=0.05))
    await cb.record(Outcome.FAILURE)
    assert cb.state is BreakerState.OPEN
    # 0.2s vs open_duration=0.05s: a 3x margin absorbs event-loop
    # scheduling jitter under a loaded full-suite run (10ms used to
    # flake on Windows).
    await asyncio.sleep(0.2)
    await cb.check()  # elapsed >= open_duration → HALF_OPEN, admitted
    assert cb.state is BreakerState.HALF_OPEN


async def test_half_open_success_closes_and_clears():
    cb = CircuitBreaker(_cfg(min_samples=1, open_duration=0.05))
    await cb.record(Outcome.FAILURE)
    # 0.2s vs open_duration=0.05s: a 3x margin absorbs event-loop
    # scheduling jitter under a loaded full-suite run (10ms used to
    # flake on Windows).
    await asyncio.sleep(0.2)
    await cb.check()
    assert cb.state is BreakerState.HALF_OPEN
    await cb.record(Outcome.SUCCESS)
    assert cb.state is BreakerState.CLOSED
    assert cb.error_rate() == 0.0  # samples cleared on recovery


async def test_half_open_failure_reopens():
    cb = CircuitBreaker(_cfg(min_samples=1, open_duration=0.05))
    await cb.record(Outcome.FAILURE)
    # 0.2s vs open_duration=0.05s: a 3x margin absorbs event-loop
    # scheduling jitter under a loaded full-suite run (10ms used to
    # flake on Windows).
    await asyncio.sleep(0.2)
    await cb.check()
    assert cb.state is BreakerState.HALF_OPEN
    await cb.record(Outcome.FAILURE)
    assert cb.state is BreakerState.OPEN


async def test_half_open_vetoes_extra_probes():
    # half_open_max_probes=1: the first probe is admitted, a second
    # concurrent check() before the first resolves is vetoed.
    cb = CircuitBreaker(_cfg(min_samples=1, open_duration=0.05, half_open_max_probes=1))
    await cb.record(Outcome.FAILURE)
    # 0.2s vs open_duration=0.05s: a 3x margin absorbs event-loop
    # scheduling jitter under a loaded full-suite run (10ms used to
    # flake on Windows).
    await asyncio.sleep(0.2)
    await cb.check()  # inflight now 1
    with pytest.raises(BreakerOpen) as ei:
        await cb.check()
    assert ei.value.state is BreakerState.HALF_OPEN


async def test_record_coerces_healthy_status_to_success():
    cb = CircuitBreaker(
        _cfg(min_samples=1, failure_codes=frozenset({500}))
    )
    await cb.record(Outcome.FAILURE, status_code=200)  # 200 ∉ {500} → SUCCESS
    assert cb.state is BreakerState.CLOSED
    assert cb.error_rate() == 0.0
    await cb.record(Outcome.FAILURE, status_code=500)  # real failure
    assert cb.state is BreakerState.OPEN


async def test_disabled_breaker_is_noop():
    cb = CircuitBreaker(BreakerConfig(enabled=False))
    for _ in range(10):
        await cb.record(Outcome.FAILURE)
    await cb.check()  # never vetoes
    assert cb.state is BreakerState.CLOSED
    assert cb.error_rate() == 0.0


# ---------------------------------------------------------------------------
# CircuitBreakerRegistry
# ---------------------------------------------------------------------------


async def test_registry_isolates_keys():
    reg = CircuitBreakerRegistry()
    a1 = await reg.get_or_create("tool:foo")
    a2 = await reg.get_or_create("tool:foo")
    b = await reg.get_or_create("tool:bar")
    assert a1 is a2
    assert a1 is not b
    assert set(reg.all_states()) == {"tool:foo", "tool:bar"}
    assert reg.get("tool:missing") is None


async def test_registry_uses_caller_config_only_on_create():
    reg = CircuitBreakerRegistry()
    cfg = _cfg(min_samples=42)
    cb = await reg.get_or_create("llm", cfg)
    assert cb.config.min_samples == 42
    # Second call ignores the config arg — returns the existing instance.
    cb2 = await reg.get_or_create("llm", _cfg(min_samples=1))
    assert cb2 is cb
    assert cb2.config.min_samples == 42


# ---------------------------------------------------------------------------
# classify_exception
# ---------------------------------------------------------------------------


def test_classify_retryable_exceptions():
    cases = [
        LLMStreamTimeout(5),
        TimeoutError(),
        ConnectionError("refused"),
        LLMError("rate limited", status_code=429),
        LLMError("boom", status_code=500),
        LLMError("bad gw", status_code=502),
        LLMError("unavailable", status_code=503),
        LLMError("gateway timeout", status_code=504),
        LLMError("no status — flattened transport error"),
        LLMError("unknown code", status_code=599),
    ]
    for exc in cases:
        assert classify_exception(exc) is Disposition.RETRYABLE, repr(exc)


def test_classify_terminal_exceptions():
    for code in (400, 401, 403, 404, 422):
        assert (
            classify_exception(LLMError("bad", status_code=code))
            is Disposition.TERMINAL
        )
    assert classify_exception(ValueError("not an LLM error")) is Disposition.TERMINAL
    assert classify_exception(KeyError("x")) is Disposition.TERMINAL


def test_classify_breaker_open_is_terminal_regardless_of_status():
    """R19: a breaker shedding load is TERMINAL — retrying hammers an open
    breaker that said "stop". The flag overrides the usual status matrix."""

    # Normally-retryable 503 becomes terminal when the breaker raised it.
    assert (
        classify_exception(
            LLMError("breaker open", status_code=503, breaker_open=True)
        )
        is Disposition.TERMINAL
    )
    # Same status without the flag stays retryable (real upstream 503).
    assert (
        classify_exception(LLMError("upstream 503", status_code=503))
        is Disposition.RETRYABLE
    )
    # breaker_open wins even with no status_code (normally retryable).
    assert (
        classify_exception(LLMError("breaker open, no code", breaker_open=True))
        is Disposition.TERMINAL
    )


# ---------------------------------------------------------------------------
# with_retry
# ---------------------------------------------------------------------------


def _rec_sleep(log: list[float]):
    async def _sleep(d: float) -> None:
        log.append(d)

    return _sleep


async def test_retry_succeeds_on_second_attempt():
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise LLMError("503", status_code=503)
        return "ok"

    sleeps: list[float] = []
    res = await with_retry(
        factory,
        RetryPolicy(max_attempts=3, base_delay=0.001, max_delay=0.01),
        sleep=_rec_sleep(sleeps),
    )
    assert res == "ok"
    assert calls == 2
    assert len(sleeps) == 1


async def test_retry_exhausted_reraises_original():
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        raise LLMError("always 503", status_code=503)

    sleeps: list[float] = []
    with pytest.raises(LLMError) as ei:
        await with_retry(
            factory,
            RetryPolicy(max_attempts=3, base_delay=0.001, max_delay=0.01),
            sleep=_rec_sleep(sleeps),
        )
    assert "always 503" in str(ei.value)
    assert calls == 3
    assert len(sleeps) == 2  # attempts 1 and 2 slept; attempt 3 raised


async def test_terminal_exception_is_not_retried():
    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        raise LLMError("bad request", status_code=400)

    sleeps: list[float] = []
    with pytest.raises(LLMError):
        await with_retry(
            factory,
            RetryPolicy(max_attempts=5),
            sleep=_rec_sleep(sleeps),
        )
    assert calls == 1
    assert sleeps == []


async def test_breaker_open_error_is_not_retried():
    """R19: with_retry fast-fails on a breaker-open error — one call, no
    sleep — instead of burning the backoff budget hammering an open breaker."""

    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        raise LLMError("breaker open", status_code=503, breaker_open=True)

    sleeps: list[float] = []
    with pytest.raises(LLMError) as ei:
        await with_retry(
            factory,
            RetryPolicy(max_attempts=5, base_delay=0.001),
            sleep=_rec_sleep(sleeps),
        )
    assert ei.value.breaker_open is True
    assert calls == 1  # never retried
    assert sleeps == []  # never slept


async def test_on_retry_sync_callback_fires():
    events: list[tuple[int, str, float]] = []

    def cb(attempt, exc, delay):
        events.append((attempt, type(exc).__name__, delay))

    async def factory():
        raise LLMError("503", status_code=503)

    with pytest.raises(LLMError):
        await with_retry(
            factory,
            RetryPolicy(max_attempts=2, base_delay=0.01),
            on_retry=cb,
            sleep=_rec_sleep([]),
        )
    assert len(events) == 1
    assert events[0][0] == 1  # fired before the 1st retry
    assert events[0][1] == "LLMError"


async def test_on_retry_async_callback_fires():
    seen: list[int] = []

    async def cb(attempt, exc, delay):
        seen.append(attempt)

    async def factory():
        raise LLMError("503", status_code=503)

    with pytest.raises(LLMError):
        await with_retry(
            factory,
            RetryPolicy(max_attempts=3, base_delay=0.001),
            on_retry=cb,
            sleep=_rec_sleep([]),
        )
    assert seen == [1, 2]


async def test_on_retry_error_is_swallowed():
    """A buggy observer must not abort the retry loop."""

    def cb(attempt, exc, delay):
        raise RuntimeError("observer blew up")

    calls = 0

    async def factory():
        nonlocal calls
        calls += 1
        if calls < 2:
            raise LLMError("503", status_code=503)
        return "recovered"

    res = await with_retry(
        factory,
        RetryPolicy(max_attempts=3, base_delay=0.001),
        on_retry=cb,
        sleep=_rec_sleep([]),
    )
    assert res == "recovered"


async def test_retry_uses_default_policy_when_none():
    async def factory():
        return "done"

    res = await with_retry(factory, None, sleep=_rec_sleep([]))
    assert res == "done"


def test_delay_for_jitter_bounds():
    """_delay_for must stay within ±jitter of the capped backoff."""

    policy = RetryPolicy(
        max_attempts=5, base_delay=10.0, max_delay=10.0, jitter_factor=0.25
    )
    for attempt in range(6):
        d = _delay_for(policy, attempt)
        # raw grows (10,20,40,...) but caps at max_delay=10; jitter ±25%.
        assert 7.5 <= d <= 12.5, (attempt, d)


async def test_retry_records_jittered_delays_in_bounds():
    policy = RetryPolicy(
        max_attempts=5, base_delay=10.0, max_delay=10.0, jitter_factor=0.25
    )
    delays: list[float] = []

    async def factory():
        raise LLMError("503", status_code=503)

    with pytest.raises(LLMError):
        await with_retry(
            factory, policy, sleep=_rec_sleep(delays)
        )
    assert len(delays) == 4  # 4 retries before the 5th attempt raises
    for d in delays:
        assert 7.5 <= d <= 12.5


def test_retry_policy_presets():
    llm = RetryPolicy.llm()
    tool = RetryPolicy.tool()
    assert llm.max_attempts == 3
    assert tool.max_attempts == 2
    assert tool.max_delay <= llm.max_delay


# ---------------------------------------------------------------------------
# Observer (R20) — breaker state-transition + outcome hooks
# ---------------------------------------------------------------------------


class _RecordingObserver(Observer):
    """Captures every transition + outcome for assertion."""

    def __init__(self) -> None:
        self.transitions: list[tuple[BreakerState, BreakerState, str]] = []
        self.outcomes: list[Outcome] = []

    def on_state_change(
        self, old: BreakerState, new: BreakerState, reason: str
    ) -> None:
        self.transitions.append((old, new, reason))

    def on_outcome(self, outcome: Outcome) -> None:
        self.outcomes.append(outcome)


async def test_observer_fires_trip_transition() -> None:
    """R20: CLOSED→OPEN emits on_state_change with the stable ``trip`` reason."""

    obs = _RecordingObserver()
    cb = CircuitBreaker(_cfg(min_samples=1), observer=obs)
    await cb.record(Outcome.FAILURE)
    assert obs.transitions == [(BreakerState.CLOSED, BreakerState.OPEN, "trip")]
    assert obs.outcomes == [Outcome.FAILURE]


async def test_observer_fires_open_elapsed_transition() -> None:
    """R20: OPEN→HALF_OPEN after cool-down carries ``open_elapsed``."""

    obs = _RecordingObserver()
    cb = CircuitBreaker(_cfg(min_samples=1, open_duration=0.05), observer=obs)
    await cb.record(Outcome.FAILURE)
    obs.transitions.clear()
    # 0.2s vs open_duration=0.05s: a 3x margin absorbs event-loop
    # scheduling jitter under a loaded full-suite run (10ms used to
    # flake on Windows).
    await asyncio.sleep(0.2)
    await cb.check()  # elapsed ≥ open_duration → HALF_OPEN
    assert obs.transitions == [
        (BreakerState.OPEN, BreakerState.HALF_OPEN, "open_elapsed")
    ]


async def test_observer_fires_probe_success_transition() -> None:
    """R20: HALF_OPEN→CLOSED on a successful probe carries ``probe_success``."""

    obs = _RecordingObserver()
    cb = CircuitBreaker(_cfg(min_samples=1, open_duration=0.05), observer=obs)
    await cb.record(Outcome.FAILURE)
    # 0.2s vs open_duration=0.05s: a 3x margin absorbs event-loop
    # scheduling jitter under a loaded full-suite run (10ms used to
    # flake on Windows).
    await asyncio.sleep(0.2)
    await cb.check()  # → HALF_OPEN
    obs.transitions.clear()
    await cb.record(Outcome.SUCCESS)
    assert obs.transitions == [
        (BreakerState.HALF_OPEN, BreakerState.CLOSED, "probe_success")
    ]


async def test_observer_fires_probe_failure_transition() -> None:
    """R20: HALF_OPEN→OPEN on a failed probe carries ``probe_failure``."""

    obs = _RecordingObserver()
    cb = CircuitBreaker(_cfg(min_samples=1, open_duration=0.05), observer=obs)
    await cb.record(Outcome.FAILURE)
    # 0.2s vs open_duration=0.05s: a 3x margin absorbs event-loop
    # scheduling jitter under a loaded full-suite run (10ms used to
    # flake on Windows).
    await asyncio.sleep(0.2)
    await cb.check()  # → HALF_OPEN
    obs.transitions.clear()
    await cb.record(Outcome.FAILURE)
    assert obs.transitions == [
        (BreakerState.HALF_OPEN, BreakerState.OPEN, "probe_failure")
    ]


async def test_observer_outcome_fires_every_record() -> None:
    """on_outcome fires once per record() call — success or failure alike."""

    obs = _RecordingObserver()
    cb = CircuitBreaker(_cfg(min_samples=10), observer=obs)  # won't trip
    await cb.record(Outcome.SUCCESS)
    await cb.record(Outcome.FAILURE)
    assert obs.outcomes == [Outcome.SUCCESS, Outcome.FAILURE]
    assert obs.transitions == []  # never tripped


async def test_observer_default_is_noop() -> None:
    """Without an explicit observer the breaker uses NoopObserver: zero
    transitions captured, zero overhead, and a trip still completes cleanly."""

    cb = CircuitBreaker(_cfg(min_samples=1))  # NoopObserver default
    await cb.record(Outcome.FAILURE)
    await cb.record(Outcome.FAILURE)
    await cb.record(Outcome.FAILURE)  # trip — no observer to notify
    assert cb.state is BreakerState.OPEN


async def test_observer_fault_is_fail_open() -> None:
    """R20: a buggy observer that raises on every hook must NOT corrupt the
    breaker state machine — ``_notify_*`` wraps each call in try/except."""

    class _Boom(Observer):
        def on_state_change(self, old, new, reason):  # noqa: ANN001
            raise RuntimeError("boom")

        def on_outcome(self, outcome):  # noqa: ANN001
            raise RuntimeError("boom")

    cb = CircuitBreaker(_cfg(min_samples=1), observer=_Boom())
    await cb.record(Outcome.FAILURE)  # trip path notifies + records outcome
    assert cb.state is BreakerState.OPEN  # state machine intact despite fault


async def test_attach_observer_replaces_active_observer() -> None:
    """attach_observer swaps the observer; subsequent events route to the new
    one only — the idempotent re-attach contract core.py relies on each turn."""

    obs1 = _RecordingObserver()
    cb = CircuitBreaker(_cfg(min_samples=1), observer=obs1)
    obs2 = _RecordingObserver()
    cb.attach_observer(obs2)  # replace
    await cb.record(Outcome.FAILURE)
    assert obs1.outcomes == []  # detached
    assert obs2.outcomes == [Outcome.FAILURE]  # active
