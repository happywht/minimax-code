"""Tests for sampler.config (R195, ``xai-grok-sampler`` ``src/config.rs`` subset).

Covers the two migrated pure types (:class:`OriginClientInfo` +
:class:`AuthScheme`) + the barrel surface + value semantics, plus the
**R193 source-of-truth reversal assertion**: ``grok_http.OriginClientInfo`` is
the *same object* as ``sampler.config.OriginClientInfo`` (the dependency
direction is inverted, not duplicated). The deferred/YAGNI symbols
(``SamplerConfig`` / ``RetryPolicy`` / 2 traits) are documented in
``config.py``. The constants ``DEFAULT_MAX_RETRIES`` / ``RATE_LIMIT_RETRY_
THRESHOLD`` migrated in R198 (:mod:`minimax_code.sampler.retry`); the package
barrel (tested below) re-exports the ``config`` (R195) + ``retry`` (R198
backoff + R199 decision layer) + ``types`` (R199 SamplingError) + ``doom_loop``
(R200 wire contract + tolerant parsers) + ``messages`` (R201 stop-reason + usage
+ delta-body cluster) leaves.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code import sampler
from minimax_code.grok_http import OriginClientInfo as GrokHttpOriginClientInfo
from minimax_code.sampler import DEFAULT_AUTH_SCHEME, AuthScheme, OriginClientInfo
from minimax_code.sampler import config as sampler_config

# ---------------------------------------------------------------------------
# Barrel surface (package + module).
# ---------------------------------------------------------------------------


def test_package_barrel_exposes_config_retry_types_symbols() -> None:
    """R195 config (3: 2 types + 1 default) + R198 retry (10: 5 constants + 5
    functions) + R199 retry decision layer (10: 7 decision classes + 3 functions)
    + R199 types (6: 1 constant + 4 types + 1 free function) + R200 doom_loop
    (17: 5 constants/fixtures + 10 classes + 2 free functions) + R201 messages
    (16: StopReason union base + 8 variants + MessagesUsage + MessageDeltaUsage
    + StopDetails + MessageDeltaBody + StreamError + parse_stop_reason +
    stop_reason_to_wire) = 62 re-exported symbols. The config trio stays; R198
    adds the backoff/max-retries leaf; R199 adds the decision layer (consuming
    the migrated SamplingError) and the SamplingError type leaf itself; R200
    adds the doom-loop wire contract + tolerant parsers (whose parsed ``raw``
    labels feed the R199 DoomLoopDetected variant); R201 adds the Messages API
    stop-reason + usage + delta-body cluster."""
    assert len(sampler.__all__) == 62
    assert set(sampler.__all__) == {
        # config (R195): 2 types + 1 default constant
        "AuthScheme",
        "DEFAULT_AUTH_SCHEME",
        "OriginClientInfo",
        # retry (R198): 5 constants + 5 functions
        "BACKOFF_BASE_MS",
        "BACKOFF_CAP_MS",
        "DEFAULT_MAX_RETRIES",
        "DOOM_LOOP_BOUND_MS",
        "RATE_LIMIT_RETRY_THRESHOLD",
        "backoff_base_ms",
        "doom_loop_backoff",
        "resolve_max_retries",
        "resolve_max_retries_with_env",
        "retry_backoff_with_jitter",
        # retry (R199 decision layer): 7 decision classes + 3 functions
        "EmitToSession",
        "Fatal",
        "Retry",
        "RetryDecision",
        "RetryWithBackoff",
        "RetryWithClientRebuild",
        "RetryWithImageStrip",
        "classify_error",
        "clone_error",
        "format_sampling_error",
        # types (R199): 1 constant + 4 types + 1 free function
        "EmptyReason",
        "EmptyResponseContext",
        "ResponseModelMetadata",
        "SERIALIZATION_DISPLAY_PREFIX",
        "SamplingError",
        "is_context_length_error",
        # doom_loop (R200): 5 constants/fixtures + 10 classes + 2 free functions
        "DOOM_LOOP_CHECK_EVENT_TYPE",
        "DOOM_LOOP_CHECK_HEADER",
        "SAMPLE_CHECK_EVENT_DATA",
        "SAMPLE_CHECK_EVENT_DATA_CUMULATIVE",
        "THINKING_CHANNEL",
        "CheckEvent",
        "DoomLoopPeek",
        "DoomLoopRecoveryPolicy",
        "DoomLoopSignal",
        "DoomLoopSignalKind",
        "LowLogprob",
        "NoDoomLoop",
        "ResponseField",
        "TailRepetition",
        "Unknown",
        "is_check_event",
        "peek_doom_loop",
        # messages (R201): StopReason union base + 8 variants + 2 usage types +
        # StopDetails + MessageDeltaBody + StreamError + 2 free functions
        "EndTurn",
        "MaxTokens",
        "MessageDeltaBody",
        "MessageDeltaUsage",
        "MessagesUsage",
        "ModelContextWindowExceeded",
        "PauseTurn",
        "Refusal",
        "StopDetails",
        "StopReason",
        "StopSequence",
        "StreamError",
        "ToolUse",
        "UnknownStopReason",
        "parse_stop_reason",
        "stop_reason_to_wire",
    }


def test_module_barrel_exposes_three_symbols() -> None:
    assert len(sampler_config.__all__) == 3
    assert set(sampler_config.__all__) == {"AuthScheme", "DEFAULT_AUTH_SCHEME", "OriginClientInfo"}


# ---------------------------------------------------------------------------
# AuthScheme: serde ``#[serde(rename_all="snake_case")]`` labels + default.
# ---------------------------------------------------------------------------


def test_auth_scheme_labels_match_serde_snake_case() -> None:
    """grok ``Bearer`` -> ``bearer``; ``XApiKey`` -> ``x_api_key`` (snake_case rename)."""
    assert AuthScheme.BEARER == "bearer"
    assert AuthScheme.X_API_KEY == "x_api_key"


def test_auth_scheme_str_is_wire_value() -> None:
    """``StrEnum.__str__`` returns the wire value (what serde would emit)."""
    assert str(AuthScheme.BEARER) == "bearer"
    assert str(AuthScheme.X_API_KEY) == "x_api_key"


def test_auth_scheme_has_exactly_two_variants() -> None:
    assert {kind.value for kind in AuthScheme} == {"bearer", "x_api_key"}


def test_default_auth_scheme_is_bearer() -> None:
    """grok ``#[default] Bearer`` is mirrored by :data:`DEFAULT_AUTH_SCHEME`."""
    assert DEFAULT_AUTH_SCHEME is AuthScheme.BEARER
    assert DEFAULT_AUTH_SCHEME == "bearer"


# ---------------------------------------------------------------------------
# OriginClientInfo: shape + value semantics (frozen + slots + hashable).
# ---------------------------------------------------------------------------


def test_origin_client_info_defaults_version_none() -> None:
    info = OriginClientInfo(product="grok-desktop")
    assert info.product == "grok-desktop"
    assert info.version is None


def test_origin_client_info_round_trips_product_and_version() -> None:
    info = OriginClientInfo(product="grok-web", version="1.2.3")
    assert info == OriginClientInfo(product="grok-web", version="1.2.3")


def test_origin_client_info_is_frozen() -> None:
    """``@dataclass(frozen=True)`` -> mutation raises (mirrors grok's immutable struct)."""
    info = OriginClientInfo(product="x", version="1")
    with pytest.raises(FrozenInstanceError):
        info.product = "y"  # type: ignore[misc]


def test_origin_client_info_is_hashable_and_equal() -> None:
    a = OriginClientInfo(product="x", version="1")
    b = OriginClientInfo(product="x", version="1")
    assert a == b
    assert hash(a) == hash(b)


def test_origin_client_info_declares_slots() -> None:
    """``slots=True`` -> the class declares ``__slots__`` over its fields (no
    per-instance ``__dict__``). Combined with ``frozen=True`` (see
    :func:`test_origin_client_info_is_frozen`) the attribute namespace is closed."""
    assert OriginClientInfo.__slots__ == ("product", "version")
    info = OriginClientInfo(product="x")
    assert not hasattr(info, "__dict__")


# ---------------------------------------------------------------------------
# R193 source-of-truth reversal: grok_http re-exports the SAME object.
# ---------------------------------------------------------------------------


def test_grok_http_origin_client_info_is_sampler_origin_client_info() -> None:
    """The R193 local definition is retired; ``grok_http`` imports the faithful
    source from here (dependency direction inverted, not duplicated)."""
    assert GrokHttpOriginClientInfo is OriginClientInfo


def test_grok_http_origin_client_info_remains_in_all() -> None:
    """Backward compat: ``from minimax_code.grok_http import OriginClientInfo`` still
    works (re-export preserved in ``grok_http.__all__``)."""
    import minimax_code.grok_http as grok_http

    assert "OriginClientInfo" in grok_http.__all__
    assert grok_http.OriginClientInfo is OriginClientInfo
