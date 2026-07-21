"""Tests for sampler.types (R199, ``xai-grok-sampling-types`` ``error.rs``).

Covers the migrated pure type leaf: :class:`EmptyReason` (StrEnum wire labels),
:class:`ResponseModelMetadata` / :class:`EmptyResponseContext` value semantics,
:func:`is_context_length_error` (5-pattern message matcher), the 11-variant
:class:`SamplingError` discriminated union (construction + value semantics),
the central predicate/accessor matrix (``is_auth_error`` / ``is_rate_limited`` /
``is_payload_too_large`` / ``is_encrypted_content_error`` /
``is_image_processing_error`` / ``is_context_length_error`` / ``is_retryable`` /
``retry_after`` / ``should_retry_header``), and the :class:`Serialization`
``__str__`` + ``serialization_message`` + ``serialization_from_rendered``
round-trip contract. The ``Http`` / ``Serialization`` variants are purified to
``str`` (no reqwest/serde_json objects), so clone/frozen semantics are plain
``str`` value semantics.

Mirrors grok's ``error.rs`` tests (the ``is_auth_error`` 403-exclusion policy,
the ``is_retryable`` status set ``{429, 500, 502, 503, 504, 520}``, the
``is_context_length_error`` case-insensitive message matchers).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler.types import (
    SERIALIZATION_DISPLAY_PREFIX,
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
    is_context_length_error,
)


def _ctx(
    *,
    reason: EmptyReason = EmptyReason.REASONING_ONLY,
    had_reasoning: bool = True,
    content_len: int = 0,
    tool_call_count: int = 0,
    finish_reason: str | None = None,
    completion_tokens: int | None = 100,
    reasoning_tokens: int | None = 50,
    prompt_tokens: int | None = 200,
    model: str = "grok-4",
    first_choice_seen: bool = True,
) -> EmptyResponseContext:
    """Build an :class:`EmptyResponseContext` with sane defaults."""
    return EmptyResponseContext(
        reason=reason,
        had_reasoning=had_reasoning,
        content_len=content_len,
        tool_call_count=tool_call_count,
        finish_reason=finish_reason,
        completion_tokens=completion_tokens,
        reasoning_tokens=reasoning_tokens,
        prompt_tokens=prompt_tokens,
        model=model,
        first_choice_seen=first_choice_seen,
    )


# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_seventeen_symbols() -> None:
    """11 SamplingError variants + base + 3 supporting types + 1 StrEnum + 1
    constant + 1 free function = 17 re-exported symbols."""
    import minimax_code.sampler.types as types

    assert len(types.__all__) == 17
    assert set(types.__all__) == {
        "Api",
        "Auth",
        "DoomLoopDetected",
        "EmptyReason",
        "EmptyResponse",
        "EmptyResponseContext",
        "EventStreamError",
        "Http",
        "IdleTimeout",
        "InvalidConfiguration",
        "MaxTokensTruncation",
        "ResponseModelMetadata",
        "SERIALIZATION_DISPLAY_PREFIX",
        "SamplingError",
        "Serialization",
        "StreamError",
        "is_context_length_error",
    }


def test_serialization_display_prefix_value() -> None:
    assert SERIALIZATION_DISPLAY_PREFIX == "serialization error: "


# ---------------------------------------------------------------------------
# EmptyReason: serde ``#[serde(rename_all="snake_case")]`` labels.
# ---------------------------------------------------------------------------


def test_empty_reason_labels_match_serde_snake_case() -> None:
    assert EmptyReason.REASONING_ONLY == "reasoning_only"
    assert EmptyReason.NO_VISIBLE_CONTENT == "no_visible_content"


def test_empty_reason_str_is_wire_value() -> None:
    assert str(EmptyReason.REASONING_ONLY) == "reasoning_only"
    assert str(EmptyReason.NO_VISIBLE_CONTENT) == "no_visible_content"


def test_empty_reason_has_exactly_two_variants() -> None:
    assert {reason.value for reason in EmptyReason} == {
        "reasoning_only",
        "no_visible_content",
    }


# ---------------------------------------------------------------------------
# ResponseModelMetadata: 3 Optional fields, #[derive(Default)].
# ---------------------------------------------------------------------------


def test_response_model_metadata_defaults_all_none() -> None:
    """grok ``#[derive(Default)]`` -> every ``Option`` field defaults to None."""
    meta = ResponseModelMetadata()
    assert meta.context_window is None
    assert meta.max_completion_tokens is None
    assert meta.models_etag is None


def test_response_model_metadata_round_trips() -> None:
    meta = ResponseModelMetadata(
        context_window=128_000, max_completion_tokens=8192, models_etag="v3"
    )
    assert meta == ResponseModelMetadata(
        context_window=128_000, max_completion_tokens=8192, models_etag="v3"
    )


def test_response_model_metadata_is_frozen_and_hashable() -> None:
    meta = ResponseModelMetadata(context_window=128_000)
    with pytest.raises(FrozenInstanceError):
        meta.context_window = 256_000  # type: ignore[misc]
    assert hash(meta) == hash(ResponseModelMetadata(context_window=128_000))


# ---------------------------------------------------------------------------
# EmptyResponseContext: 10 fields + finish_reason_str accessor.
# ---------------------------------------------------------------------------


def test_empty_response_context_finish_reason_str_none_falls_back() -> None:
    """grok accessor: ``finish_reason`` or ``"none"`` when absent."""
    ctx = _ctx(finish_reason=None)
    assert ctx.finish_reason_str() == "none"


def test_empty_response_context_finish_reason_str_present() -> None:
    ctx = _ctx(finish_reason="length")
    assert ctx.finish_reason_str() == "length"


def test_empty_response_context_is_frozen() -> None:
    ctx = _ctx()
    with pytest.raises(FrozenInstanceError):
        ctx.model = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# is_context_length_error: 5-pattern, case-insensitive message matcher.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "too long for this model",
        "prompt is too long",
        "maximum prompt length",
        "maximum context length",
        "context_length_exceeded",
        # Case-insensitive (grok ``to_ascii_lowercase``).
        "Too Long For This Model",
        "PROMPT IS TOO LONG",
        "Error: context_length_exceeded (code 1)",
    ],
)
def test_is_context_length_error_matches(message: str) -> None:
    assert is_context_length_error(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "",
        "random server error",
        "rate limited",
        "encrypted_content present",
        "Could not process image",
        "length",  # substring of "maximum prompt length" but not a standalone match
    ],
)
def test_is_context_length_error_rejects(message: str) -> None:
    assert is_context_length_error(message) is False


# ---------------------------------------------------------------------------
# SamplingError: 11-variant construction + value semantics.
# ---------------------------------------------------------------------------


def test_auth_carries_message() -> None:
    err: SamplingError = Auth("invalid api key")
    assert isinstance(err, Auth)
    assert err.message == "invalid api key"


def test_api_carries_status_message_and_headers() -> None:
    err = Api(status=429, message="slow down", retry_after_secs=30, should_retry=True)
    assert err.status == 429
    assert err.message == "slow down"
    assert err.retry_after_secs == 30
    assert err.should_retry is True
    assert err.model_metadata is None  # default


def test_doom_loop_carries_triggers_and_optional_chunk() -> None:
    err = DoomLoopDetected(triggers=("reasoning_loop",))
    assert err.triggers == ("reasoning_loop",)
    assert err.aborted_at_chunk is None  # default
    aborted = DoomLoopDetected(triggers=("a", "b"), aborted_at_chunk=42)
    assert aborted.aborted_at_chunk == 42


def test_max_tokens_truncation_is_fieldless() -> None:
    err = MaxTokensTruncation()
    assert isinstance(err, MaxTokensTruncation)
    assert err == MaxTokensTruncation()  # fieldless -> equal


def test_empty_response_carries_context() -> None:
    ctx = _ctx()
    err = EmptyResponse(context=ctx)
    assert err.context is ctx  # frozen shared reference (clone-equivalent)


def test_stream_error_carries_type_and_message() -> None:
    err = StreamError(error_type="overloaded", message="server busy")
    assert err.error_type == "overloaded"
    assert err.message == "server busy"


@pytest.mark.parametrize(
    "err",
    [
        Auth("x"),
        InvalidConfiguration("bad"),
        Http("timeout"),
        Serialization("parse"),
        Api(status=500, message="boom"),
        EventStreamError("stream"),
        StreamError("t", "m"),
        IdleTimeout(30),
        EmptyResponse(context=_ctx()),
        MaxTokensTruncation(),
        DoomLoopDetected(triggers=("t",)),
    ],
)
def test_all_variants_are_frozen(err: SamplingError) -> None:
    """Every variant inherits ``@dataclass(frozen=True, slots=True)``."""
    # The fieldless variants (MaxTokensTruncation) have no settable attr; the
    # rest raise FrozenInstanceError on assignment.
    if hasattr(err, "__slots__") and not err.__slots__:
        return
    first_slot = err.__slots__[0]
    with pytest.raises(FrozenInstanceError):
        setattr(err, first_slot, "mutated")  # type: ignore[misc]


# ---------------------------------------------------------------------------
# is_auth_error: Auth OR Api 401 (403 deliberately excluded).
# ---------------------------------------------------------------------------


def test_is_auth_error_auth_variant() -> None:
    assert Auth("bad key").is_auth_error() is True


def test_is_auth_error_api_401() -> None:
    assert Api(status=401, message="unauthorized").is_auth_error() is True


def test_is_auth_error_api_403_excluded() -> None:
    """403 Forbidden is a policy denial, not a credential rejection -- grok
    excludes it to avoid pointless OIDC refresh."""
    assert Api(status=403, message="forbidden").is_auth_error() is False


def test_is_auth_error_other_variants_false() -> None:
    assert Api(status=500, message="boom").is_auth_error() is False
    assert Http("timeout").is_auth_error() is False


# ---------------------------------------------------------------------------
# Status predicates: rate-limited / payload-too-large.
# ---------------------------------------------------------------------------


def test_is_rate_limited_only_api_429() -> None:
    assert Api(status=429, message="slow down").is_rate_limited() is True
    assert Api(status=500, message="boom").is_rate_limited() is False
    assert EventStreamError("broken").is_rate_limited() is False


def test_is_payload_too_large_only_api_413() -> None:
    assert Api(status=413, message="too big").is_payload_too_large() is True
    assert Api(status=429, message="slow").is_payload_too_large() is False


# ---------------------------------------------------------------------------
# Message predicates: encrypted-content / image-processing.
# ---------------------------------------------------------------------------


def test_is_encrypted_content_error_api_400_with_marker() -> None:
    assert (
        Api(status=400, message="encrypted_content not supported")
        .is_encrypted_content_error()
        is True
    )


def test_is_encrypted_content_error_wrong_status_or_marker() -> None:
    assert Api(status=400, message="other error").is_encrypted_content_error() is False
    assert (
        Api(status=500, message="encrypted_content").is_encrypted_content_error()
        is False
    )


@pytest.mark.parametrize("status", [400, 500])
def test_is_image_processing_error_matches_400_and_500(status: int) -> None:
    err = Api(status=status, message="Could not process image (corrupt)")
    assert err.is_image_processing_error() is True


def test_is_image_processing_error_wrong_status_or_marker() -> None:
    assert (
        Api(status=400, message="other error").is_image_processing_error() is False
    )
    assert (
        Api(status=503, message="Could not process image")
        .is_image_processing_error()
        is False
    )


# ---------------------------------------------------------------------------
# is_context_length_error method: delegates for Api/StreamError.
# ---------------------------------------------------------------------------


def test_method_is_context_length_error_api_match() -> None:
    err = Api(status=400, message="prompt is too long")
    assert err.is_context_length_error() is True


def test_method_is_context_length_error_stream_match() -> None:
    err = StreamError(error_type="overloaded", message="context_length_exceeded")
    assert err.is_context_length_error() is True


def test_method_is_context_length_error_other_variants_false() -> None:
    assert Http("timeout").is_context_length_error() is False
    assert Auth("context_length_exceeded").is_context_length_error() is False


# ---------------------------------------------------------------------------
# is_retryable matrix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "err, expected",
    [
        # Non-retryable (deterministic / config-owned).
        (Auth("bad"), False),
        (InvalidConfiguration("bad"), False),
        (Serialization("parse"), False),
        (IdleTimeout(30), False),
        (MaxTokensTruncation(), False),
        # Retryable statuses.
        (Api(status=429, message="slow"), True),
        (Api(status=500, message="boom"), True),
        (Api(status=502, message="bg"), True),
        (Api(status=503, message="unavailable"), True),
        (Api(status=504, message="timeout"), True),
        (Api(status=520, message="cf"), True),
        # Non-retryable statuses.
        (Api(status=400, message="bad"), False),
        (Api(status=401, message="auth"), False),
        (Api(status=404, message="nf"), False),
        (Api(status=413, message="big"), False),
        # Http is retryable (purified -- reqwest introspection deferred).
        (Http("timeout"), True),
        # Stream / event / empty / doom-loop are retryable.
        (EventStreamError("broken"), True),
        (StreamError("t", "m"), True),
        (EmptyResponse(context=_ctx()), True),
        (DoomLoopDetected(triggers=("t",)), True),
    ],
)
def test_is_retryable_matrix(err: SamplingError, expected: bool) -> None:
    assert err.is_retryable() is expected


# ---------------------------------------------------------------------------
# retry_after / should_retry_header accessors.
# ---------------------------------------------------------------------------


def test_retry_after_only_api() -> None:
    assert Api(status=429, message="slow", retry_after_secs=30).retry_after() == 30
    assert Api(status=429, message="slow").retry_after() is None
    assert EventStreamError("broken").retry_after() is None


def test_should_retry_header_only_api() -> None:
    assert (
        Api(status=500, message="boom", should_retry=False).should_retry_header()
        is False
    )
    assert Api(status=500, message="boom").should_retry_header() is None
    assert Http("timeout").should_retry_header() is None


# ---------------------------------------------------------------------------
# Serialization: __str__ + serialization_message + serialization_from_rendered.
# ---------------------------------------------------------------------------


def test_serialization_str_emits_prefix_plus_message() -> None:
    """grok ``Display``: prefix + message (round-trip contract)."""
    err = Serialization(message="line 1 column 2: invalid type")
    assert str(err) == "serialization error: line 1 column 2: invalid type"


def test_serialization_message_stays_serialization() -> None:
    """``serialization_message`` rebuilds as ``Serialization`` (remains
    non-retryable), not a parent/base instance only."""
    err = Serialization.serialization_message("raw rendered")
    assert isinstance(err, Serialization)
    assert err.message == "raw rendered"


def test_serialization_from_rendered_strips_prefix() -> None:
    err = Serialization.serialization_from_rendered(
        "serialization error: line 3 column 1: trailing comma"
    )
    assert err.message == "line 3 column 1: trailing comma"


def test_serialization_from_rendered_without_prefix_is_passthrough() -> None:
    """``str.removeprefix`` is a no-op when the prefix is absent (no double-strip
    hazard; a message without the prefix is taken verbatim)."""
    err = Serialization.serialization_from_rendered("bare message")
    assert err.message == "bare message"


def test_serialization_round_trip_is_exact() -> None:
    """``serialization_from_rendered(str(s)) == s`` -- the prefix strip undoes the
    ``__str__`` prefix exactly."""
    original = Serialization(message="line 7 column 4: unexpected token")
    rebuilt = Serialization.serialization_from_rendered(str(original))
    assert rebuilt == original
