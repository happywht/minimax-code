"""Tests for sampler.retry (R198 + R199, ``xai-grok-sampler`` ``retry.rs``).

Covers the migrated backoff + max-retries decision core (R198): the
:data:`DEFAULT_MAX_RETRIES` / :data:`RATE_LIMIT_RETRY_THRESHOLD` / backoff
constants, :func:`resolve_max_retries_with_env` + :func:`resolve_max_retries`,
:func:`backoff_base_ms`, :func:`retry_backoff_with_jitter`, and
:func:`doom_loop_backoff`; plus the decision layer (R199) consuming the migrated
:class:`~minimax_code.sampler.types.SamplingError`: the :class:`RetryDecision`
union (6 variants), :func:`classify_error` (10-guard matrix),
:func:`format_sampling_error` (12-variant formatter), and :func:`clone_error`.
grok's global jitter entropy (``AtomicU64`` + ``DefaultHasher`` + thread id) is
injected as ``jitter_unit``.

Mirrors grok's own pure-logic tests: ``resolve_max_retries`` env precedence /
fallthrough, ``retry_backoff_with_jitter`` range checks (retry 1 ∈ [1.6, 2.4] s,
retry 2 ∈ [3.2, 4.8] s, retry 10 ∈ [24, 36] s), ``doom_loop_backoff`` bound, and
the ``classify_error`` guard ordering (auth/encrypted emit; 413/image strip;
x-should-retry:false + context-length fatal; doom-loop near-instant; 429 cap;
generic first-rebuild/later-backoff; else fatal).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import timedelta

import pytest

from minimax_code.sampler.retry import (
    BACKOFF_BASE_MS,
    BACKOFF_CAP_MS,
    DEFAULT_MAX_RETRIES,
    DOOM_LOOP_BOUND_MS,
    RATE_LIMIT_RETRY_THRESHOLD,
    EmitToSession,
    Fatal,
    Retry,
    RetryDecision,
    RetryWithBackoff,
    RetryWithClientRebuild,
    RetryWithImageStrip,
    backoff_base_ms,
    classify_error,
    clone_error,
    doom_loop_backoff,
    format_sampling_error,
    resolve_max_retries,
    resolve_max_retries_with_env,
    retry_backoff_with_jitter,
)
from minimax_code.sampler.types import (
    Api,
    Auth,
    DoomLoopDetected,
    EmptyReason,
    EmptyResponse,
    EmptyResponseContext,
    EventStreamError,
    Http,
    IdleTimeout,
    InvalidConfiguration,
    MaxTokensTruncation,
    ResponseModelMetadata,
    SamplingError,
    Serialization,
    StreamError,
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


# ===========================================================================
# R199 decision layer (consumes SamplingError)
# ===========================================================================


# ---------------------------------------------------------------------------
# RetryDecision: 6-variant construction + frozen value semantics.
# ---------------------------------------------------------------------------


def test_retry_decision_variants_construct() -> None:
    """All 6 decision variants carry their fields; frozen + slots."""
    assert isinstance(Retry(backoff=timedelta(seconds=2)), Retry)
    rwb = RetryWithBackoff(backoff=timedelta(seconds=1), is_rate_limited=True)
    assert rwb.is_rate_limited is True
    assert RetryWithImageStrip() == RetryWithImageStrip()  # fieldless
    assert (
        RetryWithClientRebuild(backoff=timedelta(seconds=2)).backoff
        == timedelta(seconds=2)
    )
    err = Http("timeout")
    assert EmitToSession(error=err).error is err
    assert Fatal(error=err).error is err


def test_retry_decision_variants_are_frozen() -> None:
    rwb = RetryWithBackoff(backoff=timedelta(seconds=1), is_rate_limited=True)
    with pytest.raises(FrozenInstanceError):
        rwb.backoff = timedelta(seconds=99)  # type: ignore[misc]


# ---------------------------------------------------------------------------
# classify_error: 10-guard matrix.
# ---------------------------------------------------------------------------


def _classify(err: SamplingError, **kwargs: object) -> RetryDecision:
    """Thin wrapper with the R198 default budget + threshold."""
    defaults: dict[str, object] = {
        "retry_count": 0,
        "max_retries": DEFAULT_MAX_RETRIES,
        "rate_limit_threshold": RATE_LIMIT_RETRY_THRESHOLD,
        "jitter_unit": 0.5,
    }
    defaults.update(kwargs)
    return classify_error(err, **defaults)  # type: ignore[arg-type]


# Guards 1-2: auth + encrypted-content -> EmitToSession (surface verbatim).


def test_classify_auth_emits_to_session() -> None:
    decision = _classify(Auth("bad key"))
    assert isinstance(decision, EmitToSession)
    assert isinstance(decision.error, Auth)


def test_classify_api_401_emits_to_session() -> None:
    decision = _classify(Api(status=401, message="unauthorized"))
    assert isinstance(decision, EmitToSession)


def test_classify_encrypted_content_emits_to_session() -> None:
    decision = _classify(Api(status=400, message="encrypted_content not supported"))
    assert isinstance(decision, EmitToSession)


# Guards 3-4: 413 + image-processing -> RetryWithImageStrip.


def test_classify_payload_too_large_strips_images() -> None:
    assert isinstance(_classify(Api(status=413, message="too big")), RetryWithImageStrip)


@pytest.mark.parametrize("status", [400, 500])
def test_classify_image_processing_strips_images(status: int) -> None:
    decision = _classify(Api(status=status, message="Could not process image"))
    assert isinstance(decision, RetryWithImageStrip)


# Guard 5: x-should-retry: False -> Fatal (checked AFTER image-strip guards).


def test_classify_should_retry_false_is_fatal() -> None:
    decision = _classify(Api(status=500, message="boom", should_retry=False))
    assert isinstance(decision, Fatal)


def test_classify_should_retry_false_checked_after_image_strip() -> None:
    """A 413 with ``should_retry=False`` still strips images: stripping changes
    the payload, so the original "don't retry" does not apply to the stripped
    request (load-bearing guard order)."""
    decision = _classify(Api(status=413, message="too big", should_retry=False))
    assert isinstance(decision, RetryWithImageStrip)


# Guard 6: context-length overflow -> Fatal (deterministic, never retry).


def test_classify_context_length_is_fatal() -> None:
    assert isinstance(
        _classify(Api(status=400, message="prompt is too long")), Fatal
    )


# Guard 7: doom-loop -> near-instant Retry.


def test_classify_doom_loop_retries_near_instant() -> None:
    decision = _classify(DoomLoopDetected(triggers=("loop",)))
    assert isinstance(decision, Retry)
    assert decision.backoff <= timedelta(milliseconds=DOOM_LOOP_BOUND_MS)


# Guard 8: 429 -> RetryWithBackoff, capped at min(max_retries, threshold).


def test_classify_rate_limited_retries_with_backoff() -> None:
    decision = _classify(Api(status=429, message="slow", retry_after_secs=30))
    assert isinstance(decision, RetryWithBackoff)
    assert decision.is_rate_limited is True
    # Retry-After header honored over exponential backoff.
    assert decision.backoff == timedelta(seconds=30)


def test_classify_rate_limited_uses_backoff_without_header() -> None:
    """No Retry-After header -> exponential backoff (retry 1, jitter=1.0 -> 2.4 s)."""
    decision = _classify(Api(status=429, message="slow"), jitter_unit=1.0)
    assert isinstance(decision, RetryWithBackoff)
    assert decision.backoff == timedelta(milliseconds=2400)


def test_classify_rate_limited_exhausts_to_fatal() -> None:
    """retry_count=1 -> next_attempt=2 >= threshold=2 -> Fatal."""
    assert isinstance(
        _classify(Api(status=429, message="slow"), retry_count=1), Fatal
    )


# Guard 9: generic retryable -> first rebuild / later backoff.


def test_classify_generic_retryable_first_rebuilds_client() -> None:
    decision = _classify(Http("timeout"), retry_count=0)
    assert isinstance(decision, RetryWithClientRebuild)


def test_classify_generic_retryable_later_plain_retry() -> None:
    decision = _classify(Http("timeout"), retry_count=1)
    assert isinstance(decision, Retry)
    assert not isinstance(decision, RetryWithClientRebuild)


def test_classify_generic_retryable_exhausts_to_fatal() -> None:
    """retry_count=14 -> next_attempt=15 >= max_retries=15 -> Fatal."""
    assert isinstance(
        _classify(Api(status=500, message="boom"), retry_count=14), Fatal
    )


# Guard 10: default (non-retryable, e.g. InvalidConfiguration / Serialization).


def test_classify_non_retryable_default_is_fatal() -> None:
    assert isinstance(_classify(InvalidConfiguration("bad")), Fatal)


def test_classify_serialization_is_fatal() -> None:
    assert isinstance(_classify(Serialization("parse fail")), Fatal)


# ---------------------------------------------------------------------------
# format_sampling_error: 12-variant telemetry-friendly rendering.
# ---------------------------------------------------------------------------


def test_format_includes_retry_banner_when_count_present() -> None:
    assert format_sampling_error(Auth("bad key"), retry_count=3).startswith(
        "Request failed after 3 retries. "
    )


def test_format_omits_banner_when_count_none() -> None:
    assert not format_sampling_error(Auth("bad key"), retry_count=None).startswith(
        "Request failed"
    )


@pytest.mark.parametrize(
    "err, fragment",
    [
        (Auth("bad key"), "Authentication failed: bad key"),
        (InvalidConfiguration("bad model"), "Invalid configuration: bad model"),
        (Http("connection reset"), "HTTP request failed: connection reset"),
        (Serialization("line 1: bad"), "Failed to parse API response: line 1: bad"),
        (Api(status=429, message="slow"), "API error (HTTP 429"),
        (EventStreamError("broken"), "Event stream error: broken"),
        (StreamError("overloaded", "busy"), "Server stream error (overloaded): busy"),
        (IdleTimeout(45), "stopped responding after 45s"),
        (MaxTokensTruncation(), "truncated by max_tokens"),
        (DoomLoopDetected(triggers=("a", "b")), "reasoning loop (a, b)"),
    ],
)
def test_format_variants_render_fragment(err: SamplingError, fragment: str) -> None:
    assert fragment in format_sampling_error(err, retry_count=1)


def test_format_empty_response_includes_context_fields() -> None:
    ctx = EmptyResponseContext(
        reason=EmptyReason.REASONING_ONLY,
        had_reasoning=True,
        content_len=0,
        tool_call_count=0,
        finish_reason=None,
        completion_tokens=100,
        reasoning_tokens=50,
        prompt_tokens=200,
        model="grok-4",
        first_choice_seen=True,
    )
    rendered = format_sampling_error(EmptyResponse(context=ctx), retry_count=None)
    assert "reasoning_only" in rendered
    assert "model=grok-4" in rendered
    assert "finish_reason=none" in rendered  # finish_reason_str fallback
    assert "completion_tokens=100" in rendered


def test_format_api_status_hints() -> None:
    """The Api variant appends a per-status human hint."""
    assert "(rate limited" in format_sampling_error(
        Api(status=429, message="slow"), retry_count=None
    )
    assert "(server unavailable" in format_sampling_error(
        Api(status=503, message="unavailable"), retry_count=None
    )
    assert "(bad request" in format_sampling_error(
        Api(status=400, message="bad"), retry_count=None
    )


# ---------------------------------------------------------------------------
# clone_error: per-variant reconstruction.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "err",
    [
        Auth("bad"),
        InvalidConfiguration("bad"),
        Http("timeout"),
        Serialization("parse"),
        EventStreamError("broken"),
        StreamError("t", "m"),
        IdleTimeout(30),
        EmptyResponse(
            context=EmptyResponseContext(
                reason=EmptyReason.NO_VISIBLE_CONTENT,
                had_reasoning=False,
                content_len=0,
                tool_call_count=0,
                finish_reason="length",
                completion_tokens=10,
                reasoning_tokens=0,
                prompt_tokens=5,
                model="m",
                first_choice_seen=False,
            )
        ),
        MaxTokensTruncation(),
        DoomLoopDetected(triggers=("a",), aborted_at_chunk=7),
    ],
)
def test_clone_error_round_trips_type_and_fields(err: SamplingError) -> None:
    cloned = clone_error(err)
    assert type(cloned) is type(err)
    assert cloned == err


def test_clone_error_api_preserves_all_fields() -> None:
    err = Api(
        status=429,
        message="slow",
        retry_after_secs=30,
        should_retry=True,
        model_metadata=ResponseModelMetadata(context_window=128_000),
    )
    cloned = clone_error(err)
    assert cloned == err
    assert cloned.model_metadata == err.model_metadata


def test_clone_serialization_preserves_message_and_str() -> None:
    """Regression: grok's ``Http -> EventStreamError`` downgrade is retired; the
    purified ``Serialization`` clones as-is with its message + ``__str__`` intact."""
    err = Serialization("line 1 column 2: bad")
    cloned = clone_error(err)
    assert type(cloned) is Serialization
    assert cloned.message == "line 1 column 2: bad"
    assert str(cloned) == str(err)
