"""Tests for sampler.retry (R198, ``xai-grok-sampler`` ``retry.rs`` pure core).

Covers the migrated backoff + max-retries decision core: the
:data:`DEFAULT_MAX_RETRIES` / :data:`RATE_LIMIT_RETRY_THRESHOLD` / backoff
constants, :func:`resolve_max_retries_with_env` + :func:`resolve_max_retries`,
:func:`backoff_base_ms`, :func:`retry_backoff_with_jitter`, and
:func:`doom_loop_backoff`. The SamplingError-dependent decision layer
(``RetryDecision`` + ``classify_error`` + ``format_sampling_error`` +
``clone_error``) is deferred until ``SamplingError`` migrates; grok's global
jitter entropy (``AtomicU64`` + ``DefaultHasher`` + thread id) is injected as
``jitter_unit``.

Mirrors grok's own pure-logic tests: ``resolve_max_retries`` env precedence /
fallthrough, ``retry_backoff_with_jitter`` range checks (retry 1 ∈ [1.6, 2.4] s,
retry 2 ∈ [3.2, 4.8] s, retry 10 ∈ [24, 36] s), ``doom_loop_backoff`` bound.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from minimax_code.sampler.retry import (
    BACKOFF_BASE_MS,
    BACKOFF_CAP_MS,
    DEFAULT_MAX_RETRIES,
    DOOM_LOOP_BOUND_MS,
    RATE_LIMIT_RETRY_THRESHOLD,
    backoff_base_ms,
    doom_loop_backoff,
    resolve_max_retries,
    resolve_max_retries_with_env,
    retry_backoff_with_jitter,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_default_max_retries_is_fifteen() -> None:
    """grok ``DEFAULT_MAX_RETRIES`` = 15 (retries 1-4 exponential + 5-15 flat
    ~= 6 min total -- a transient outage recovers within one budget)."""
    assert DEFAULT_MAX_RETRIES == 15


def test_rate_limit_threshold_is_two() -> None:
    """grok ``RATE_LIMIT_RETRY_THRESHOLD`` = 2 (the third 429 is fatal)."""
    assert RATE_LIMIT_RETRY_THRESHOLD == 2


def test_backoff_constants() -> None:
    assert BACKOFF_BASE_MS == 2000
    assert BACKOFF_CAP_MS == 30_000
    assert DOOM_LOOP_BOUND_MS == 250


# ---------------------------------------------------------------------------
# resolve_max_retries_with_env (pure core)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "env_override, model_max_retries, expected",
    [
        # Env wins when parseable as a non-negative u32.
        ("9", 3, 9),
        ("9", None, 9),
        ("0", 5, 0),  # explicit zero is a valid u32 (disable retries)
        # Non-numeric env falls through to model, then default.
        ("abc", 4, 4),
        ("abc", None, DEFAULT_MAX_RETRIES),
        ("", None, DEFAULT_MAX_RETRIES),  # empty string is non-numeric
        # Negative is rejected (grok parses u32).
        ("-1", 4, 4),
        ("-1", None, DEFAULT_MAX_RETRIES),
        # No env -> model -> default.
        (None, 7, 7),
        (None, None, DEFAULT_MAX_RETRIES),
    ],
)
def test_resolve_max_retries_with_env_matrix(
    env_override: str | None, model_max_retries: int | None, expected: int
) -> None:
    assert resolve_max_retries_with_env(env_override, model_max_retries) == expected


# ---------------------------------------------------------------------------
# resolve_max_retries (env wrapper)
# ---------------------------------------------------------------------------


def test_resolve_max_retries_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_MAX_RETRIES", "9")
    assert resolve_max_retries(3) == 9


def test_resolve_max_retries_invalid_env_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROK_MAX_RETRIES", "abc")
    assert resolve_max_retries(3) == 3


def test_resolve_max_retries_negative_env_falls_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """grok parses u32, so a negative GROK_MAX_RETRIES is rejected -> model."""
    monkeypatch.setenv("GROK_MAX_RETRIES", "-1")
    assert resolve_max_retries(4) == 4


def test_resolve_max_retries_unset_uses_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GROK_MAX_RETRIES", raising=False)
    assert resolve_max_retries(7) == 7


def test_resolve_max_retries_unset_no_model_uses_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROK_MAX_RETRIES", raising=False)
    assert resolve_max_retries(None) == DEFAULT_MAX_RETRIES


# ---------------------------------------------------------------------------
# backoff_base_ms (exponential base, pre-jitter)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "retry_count, expected_ms",
    [
        (0, 2000),    # saturating_sub(0,1) = 0 -> unshifted
        (1, 2000),    # first retry: 2 s
        (2, 4000),
        (3, 8000),
        (4, 16000),
        (5, 30000),   # 2000 << 4 = 32000 -> capped at 30 s
        (6, 30000),
        (10, 30000),
        (100, 30000),  # huge shift stays capped, no overflow
    ],
)
def test_backoff_base_ms_matrix(retry_count: int, expected_ms: int) -> None:
    assert backoff_base_ms(retry_count) == expected_ms


# ---------------------------------------------------------------------------
# retry_backoff_with_jitter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "retry_count, jitter_unit, expected_ms",
    [
        # retry 1: base=2000, range=400 -> [1600, 2400]
        (1, 0.0, 1600),
        (1, 0.5, 2000),
        (1, 1.0, 2400),
        # retry 0 saturates to the retry-1 schedule.
        (0, 0.0, 1600),
        (0, 1.0, 2400),
        # retry 2: base=4000, range=800 -> [3200, 4800]
        (2, 0.0, 3200),
        (2, 1.0, 4800),
        # retry 10: base=30000 (capped), range=6000 -> [24000, 36000]
        (10, 0.0, 24000),
        (10, 1.0, 36000),
    ],
)
def test_retry_backoff_with_jitter_endpoints(
    retry_count: int, jitter_unit: float, expected_ms: int
) -> None:
    assert retry_backoff_with_jitter(retry_count, jitter_unit) == timedelta(
        milliseconds=expected_ms
    )


@pytest.mark.parametrize("retry_count", [1, 2, 3, 5, 10])
def test_retry_backoff_range_invariant(retry_count: int) -> None:
    """Across the full jitter sweep the backoff stays in [base-range, base+range]."""
    base = backoff_base_ms(retry_count)
    jitter_range = base // 5
    lower = timedelta(milliseconds=base - jitter_range)
    upper = timedelta(milliseconds=base + jitter_range)
    for unit in (0.0, 0.25, 0.5, 0.75, 1.0):
        delay = retry_backoff_with_jitter(retry_count, unit)
        assert lower <= delay <= upper


# ---------------------------------------------------------------------------
# doom_loop_backoff
# ---------------------------------------------------------------------------


def test_doom_loop_backoff_endpoints() -> None:
    assert doom_loop_backoff(0.0) == timedelta(0)
    assert doom_loop_backoff(0.5) == timedelta(milliseconds=125)
    assert doom_loop_backoff(1.0) == timedelta(milliseconds=250)


@pytest.mark.parametrize("jitter_unit", [0.0, 0.1, 0.5, 0.9, 1.0])
def test_doom_loop_backoff_bound(jitter_unit: float) -> None:
    """grok ``hash % 251`` -> [0, 250] ms; never exceeds the bound."""
    delay = doom_loop_backoff(jitter_unit)
    assert timedelta(0) <= delay <= timedelta(milliseconds=DOOM_LOOP_BOUND_MS)
