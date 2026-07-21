"""Tests for sampler.events (R235, ``xai-grok-sampler`` ``src/events.rs``
whole-leaf migration) + the ``RequestId`` newtype added to ``types.py``.

Covers the four migrated public entities:

* :class:`SamplingChannel` -- the 2-variant ``Text`` / ``Reasoning`` split.
* :class:`SamplingErrorKind` -- the 9-variant wire taxonomy + the lowercase
  :meth:`as_str` telemetry label (distinct from the PascalCase wire ``value``).
* :class:`SamplingErrorInfo` -- the 9-field structured error payload (6
  required + 3 ``None``-defaulted).
* :class:`SamplingEvent` -- the 10-variant in-program tagged union (no serde,
  ``isinstance`` dispatch).

Plus :class:`~minimax_code.sampler.types.RequestId` (added to ``types.py``
this round: ``value`` + :meth:`random` UUIDv4 + :meth:`as_str` + ``__str__``)
and :func:`from_sampling_error` (the ``From<&SamplingError>`` projection that
lifts all 11 :class:`SamplingError` variants into a :class:`SamplingErrorInfo`,
rebuilding the ``Display`` message at the consumption site).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code import sampler
from minimax_code.sampler import events as sampler_events
from minimax_code.sampler import types as sampler_types
from minimax_code.sampler.events import (
    BackendToolCallCompleted,
    BackendToolCallStarted,
    ChannelToken,
    Completed,
    EventToolCallDelta,
    Failed,
    FirstToken,
    ModelMetadata,
    Retrying,
    SamplingChannel,
    SamplingErrorInfo,
    SamplingErrorKind,
    SamplingEvent,
    StreamStarted,
    from_sampling_error,
)
from minimax_code.sampler.metrics import InferenceLatencyStats
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
    RequestId,
    ResponseModelMetadata,
    Serialization,
    StreamError,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _empty_ctx() -> EmptyResponseContext:
    """A minimal valid :class:`EmptyResponseContext` for EmptyResponse tests."""
    return EmptyResponseContext(
        reason=EmptyReason.NO_VISIBLE_CONTENT,
        had_reasoning=False,
        content_len=0,
        tool_call_count=0,
        finish_reason=None,
        completion_tokens=None,
        reasoning_tokens=None,
        prompt_tokens=None,
        model="grok-test",
        first_choice_seen=False,
    )


# ---------------------------------------------------------------------------
# Barrel surface (module + package).
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_fifteen_symbols() -> None:
    assert len(sampler_events.__all__) == 15
    assert set(sampler_events.__all__) == {
        "BackendToolCallCompleted",
        "BackendToolCallStarted",
        "ChannelToken",
        "Completed",
        "EventToolCallDelta",
        "Failed",
        "FirstToken",
        "ModelMetadata",
        "Retrying",
        "SamplingChannel",
        "SamplingErrorInfo",
        "SamplingErrorKind",
        "SamplingEvent",
        "StreamStarted",
        "from_sampling_error",
    }


def test_package_barrel_re_exports_events_symbols() -> None:
    """R235 adds 15 events symbols (14 classes + from_sampling_error) + the
    RequestId newtype to the package barrel (207 -> 223; the total is asserted
    in ``test_sampler_config.test_package_barrel_exposes_...``)."""
    for name in sampler_events.__all__:
        assert name in sampler.__all__
        assert hasattr(sampler, name)
    # Identity: the package symbol IS the module symbol (re-export, not a copy).
    assert sampler.SamplingEvent is SamplingEvent
    assert sampler.from_sampling_error is from_sampling_error
    # RequestId lives in types but is re-exported through the package barrel.
    assert "RequestId" in sampler.__all__
    assert sampler.RequestId is RequestId


def test_types_module_barrel_includes_request_id() -> None:
    """RequestId was added to ``types.__all__`` this round (17 -> 18)."""
    assert "RequestId" in sampler_types.__all__
    assert len(sampler_types.__all__) == 18


# ---------------------------------------------------------------------------
# RequestId: the newtype added to types.py this round.
# ---------------------------------------------------------------------------


def test_request_id_holds_value() -> None:
    rid = RequestId("req-abc-123")
    assert rid.value == "req-abc-123"


def test_request_id_as_str_borrows_inner() -> None:
    rid = RequestId("req-abc-123")
    assert rid.as_str() == "req-abc-123"


def test_request_id_str_is_inner_verbatim() -> None:
    """grok ``Display``: the inner string verbatim (transparent newtype)."""
    assert str(RequestId("req-abc-123")) == "req-abc-123"


def test_request_id_random_is_unique() -> None:
    """``RequestId::random`` is backed by UUIDv4 -> two calls are distinct."""
    assert RequestId.random() != RequestId.random()


def test_request_id_random_yields_canonical_uuid_form() -> None:
    """UUIDv4 ``to_string`` -> the canonical 36-char ``8-4-4-4-12`` form."""
    rid = RequestId.random()
    assert isinstance(rid.value, str)
    assert len(rid.value) == 36
    # 5 groups separated by 4 hyphens.
    assert rid.value.count("-") == 4


def test_request_id_is_frozen_and_hashable() -> None:
    rid = RequestId("req-1")
    with pytest.raises(FrozenInstanceError):
        rid.value = "req-2"  # type: ignore[misc]
    a = RequestId("same")
    b = RequestId("same")
    assert a == b
    assert hash(a) == hash(b)


# ---------------------------------------------------------------------------
# SamplingChannel: the 2-variant PascalCase wire split.
# ---------------------------------------------------------------------------


def test_sampling_channel_two_variants_pascalcase() -> None:
    """grok derives ``Serialize``/``Deserialize`` without ``rename_all`` -> the
    variant name IS the wire string."""
    assert SamplingChannel.TEXT == "Text"
    assert SamplingChannel.REASONING == "Reasoning"


def test_sampling_channel_str_is_wire_value() -> None:
    assert str(SamplingChannel.TEXT) == "Text"
    assert str(SamplingChannel.REASONING) == "Reasoning"


# ---------------------------------------------------------------------------
# SamplingErrorKind: 9-variant wire taxonomy + lowercase telemetry label.
# ---------------------------------------------------------------------------


def test_sampling_error_kind_nine_variants_wire_pascalcase() -> None:
    assert SamplingErrorKind.AUTH == "Auth"
    assert SamplingErrorKind.HTTP == "Http"
    assert SamplingErrorKind.API == "Api"
    assert SamplingErrorKind.SERIALIZATION == "Serialization"
    assert SamplingErrorKind.IDLE_TIMEOUT == "IdleTimeout"
    assert SamplingErrorKind.RATE_LIMITED == "RateLimited"
    assert SamplingErrorKind.EMPTY_RESPONSE == "EmptyResponse"
    assert SamplingErrorKind.MAX_TOKENS_TRUNCATION == "MaxTokensTruncation"
    assert SamplingErrorKind.DOOM_LOOP_DETECTED == "DoomLoopDetected"
    assert len(list(SamplingErrorKind)) == 9


def test_sampling_error_kind_as_str_yields_lowercase_telemetry() -> None:
    """``as_str`` is the frozen telemetry surface (distinct from the wire value)."""
    assert SamplingErrorKind.AUTH.as_str() == "auth"
    assert SamplingErrorKind.HTTP.as_str() == "http"
    assert SamplingErrorKind.API.as_str() == "api"
    assert SamplingErrorKind.SERIALIZATION.as_str() == "serialization"
    assert SamplingErrorKind.IDLE_TIMEOUT.as_str() == "idle_timeout"
    assert SamplingErrorKind.RATE_LIMITED.as_str() == "rate_limited"
    assert SamplingErrorKind.EMPTY_RESPONSE.as_str() == "empty_response"
    assert SamplingErrorKind.MAX_TOKENS_TRUNCATION.as_str() == "max_tokens_truncation"
    assert SamplingErrorKind.DOOM_LOOP_DETECTED.as_str() == "doom_loop_detected"


def test_sampling_error_kind_as_str_distinct_from_wire_value() -> None:
    """For every variant the telemetry label differs from the wire value.

    The telemetry label is lowercase snake_case (``idle_timeout`` /
    ``rate_limited`` / ...), which diverges from the wire value's naive
    ``.lower()`` (``idletimeout`` / ``ratelimited``) for multi-word kinds --
    so only the ``!= kind.value`` invariant is asserted here (the exact
    labels are pinned by ``test_sampling_error_kind_as_str_yields_lowercase_
    telemetry``).
    """
    for kind in SamplingErrorKind:
        assert kind.as_str() != kind.value


# ---------------------------------------------------------------------------
# SamplingErrorInfo: 9-field structured payload.
# ---------------------------------------------------------------------------


def test_sampling_error_info_required_fields_round_trip() -> None:
    info = SamplingErrorInfo(
        kind=SamplingErrorKind.HTTP,
        status_code=503,
        message="boom",
        is_retryable=True,
        retry_after_secs=12,
        model_metadata=ResponseModelMetadata(context_window=8192),
    )
    assert info.kind is SamplingErrorKind.HTTP
    assert info.status_code == 503
    assert info.message == "boom"
    assert info.is_retryable is True
    assert info.retry_after_secs == 12
    assert info.model_metadata == ResponseModelMetadata(context_window=8192)


def test_sampling_error_info_three_optional_fields_default_none() -> None:
    """The three ``#[serde(default, skip_serializing_if)]`` fields."""
    info = SamplingErrorInfo(
        kind=SamplingErrorKind.AUTH,
        status_code=None,
        message="bad token",
        is_retryable=False,
        retry_after_secs=None,
        model_metadata=None,
    )
    assert info.empty_response_context is None
    assert info.doom_loop_triggers is None
    assert info.doom_loop_aborted_at_chunk is None


def test_sampling_error_info_is_frozen() -> None:
    info = SamplingErrorInfo(
        kind=SamplingErrorKind.API,
        status_code=400,
        message="x",
        is_retryable=False,
        retry_after_secs=None,
        model_metadata=None,
    )
    with pytest.raises(FrozenInstanceError):
        info.message = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# SamplingEvent: the 10-variant in-program tagged union.
# ---------------------------------------------------------------------------


def test_stream_started_carries_request_id_and_timestamp() -> None:
    rid = RequestId("req-1")
    ev = StreamStarted(request_id=rid, timestamp_ms=1_700_000_000_000)
    assert isinstance(ev, SamplingEvent)
    assert ev.request_id is rid
    assert ev.timestamp_ms == 1_700_000_000_000


def test_first_token_carries_request_id() -> None:
    rid = RequestId("req-1")
    ev = FirstToken(request_id=rid)
    assert isinstance(ev, SamplingEvent)
    assert ev.request_id is rid


def test_channel_token_carries_channel_text_index() -> None:
    rid = RequestId("req-1")
    ev = ChannelToken(request_id=rid, channel=SamplingChannel.TEXT, text="hi", chunk_index=0)
    assert isinstance(ev, SamplingEvent)
    assert ev.channel is SamplingChannel.TEXT
    assert ev.text == "hi"
    assert ev.chunk_index == 0


def test_event_tool_call_delta_optional_fields() -> None:
    """The renamed ``EventToolCallDelta`` (grok ``ToolCallDelta``); id/name/
    arguments_delta are ``str | None`` (mid-stream fragments may be absent)."""
    rid = RequestId("req-1")
    ev = EventToolCallDelta(
        request_id=rid,
        tool_index=0,
        id="call_1",
        name="search",
        arguments_delta='{"q":"a"}',
    )
    assert isinstance(ev, SamplingEvent)
    assert ev.tool_index == 0
    assert ev.id == "call_1"
    assert ev.name == "search"
    assert ev.arguments_delta == '{"q":"a"}'
    # All-None form (first delta before any fragment arrives).
    ev_none = EventToolCallDelta(
        request_id=rid, tool_index=0, id=None, name=None, arguments_delta=None
    )
    assert ev_none.id is None
    assert ev_none.name is None
    assert ev_none.arguments_delta is None


def test_completed_holds_opaque_response_and_metrics() -> None:
    """``response`` is opaque ``Any`` (grok ``Box<ConversationResponse>``)."""
    rid = RequestId("req-1")
    metrics = InferenceLatencyStats()
    ev = Completed(request_id=rid, response={"choices": []}, metrics=metrics)
    assert isinstance(ev, SamplingEvent)
    assert ev.response == {"choices": []}
    assert ev.metrics is metrics


def test_retrying_carries_attempt_kind_and_doom_loop_fields() -> None:
    rid = RequestId("req-1")
    triggers = ["repetition", "low_entropy"]
    ev = Retrying(
        request_id=rid,
        attempt=2,
        max_retries=4,
        kind=SamplingErrorKind.DOOM_LOOP_DETECTED,
        reason="doom loop",
        doom_loop_triggers=triggers,
        doom_loop_aborted_at_chunk=42,
    )
    assert isinstance(ev, SamplingEvent)
    assert ev.attempt == 2
    assert ev.max_retries == 4
    assert ev.kind is SamplingErrorKind.DOOM_LOOP_DETECTED
    assert ev.reason == "doom loop"
    assert ev.doom_loop_triggers == triggers
    assert ev.doom_loop_aborted_at_chunk == 42


def test_failed_carries_sampling_error_info() -> None:
    rid = RequestId("req-1")
    info = SamplingErrorInfo(
        kind=SamplingErrorKind.API,
        status_code=500,
        message="server error",
        is_retryable=True,
        retry_after_secs=None,
        model_metadata=None,
    )
    ev = Failed(request_id=rid, error=info)
    assert isinstance(ev, SamplingEvent)
    assert ev.error is info


def test_model_metadata_carries_response_model_metadata() -> None:
    rid = RequestId("req-1")
    md = ResponseModelMetadata(context_window=131072, max_completion_tokens=8192)
    ev = ModelMetadata(request_id=rid, metadata=md)
    assert isinstance(ev, SamplingEvent)
    assert ev.metadata is md


def test_backend_tool_call_started_carries_call_id_and_name() -> None:
    rid = RequestId("req-1")
    ev = BackendToolCallStarted(request_id=rid, call_id="call_1", name="search")
    assert isinstance(ev, SamplingEvent)
    assert ev.call_id == "call_1"
    assert ev.name == "search"


def test_backend_tool_call_completed_result_is_opaque_any() -> None:
    """``result`` is opaque ``Any`` (grok ``Option<serde_json::Value>``)."""
    rid = RequestId("req-1")
    ev_with = BackendToolCallCompleted(
        request_id=rid, call_id="call_1", name="search", result={"hits": [1, 2]}
    )
    assert isinstance(ev_with, SamplingEvent)
    assert ev_with.result == {"hits": [1, 2]}
    ev_none = BackendToolCallCompleted(
        request_id=rid, call_id="call_1", name="search", result=None
    )
    assert ev_none.result is None


def test_all_event_variants_are_subclasses_of_base() -> None:
    """``isinstance`` dispatch is the union's consumption contract."""
    rid = RequestId("req-1")
    for ev in (
        StreamStarted(request_id=rid, timestamp_ms=0),
        FirstToken(request_id=rid),
        ChannelToken(request_id=rid, channel=SamplingChannel.TEXT, text="", chunk_index=0),
        EventToolCallDelta(request_id=rid, tool_index=0, id=None, name=None, arguments_delta=None),
        Completed(request_id=rid, response=None, metrics=InferenceLatencyStats()),
        Retrying(
            request_id=rid,
            attempt=1,
            max_retries=1,
            kind=SamplingErrorKind.HTTP,
            reason="x",
            doom_loop_triggers=None,
            doom_loop_aborted_at_chunk=None,
        ),
        Failed(
            request_id=rid,
            error=SamplingErrorInfo(
                kind=SamplingErrorKind.HTTP,
                status_code=None,
                message="x",
                is_retryable=True,
                retry_after_secs=None,
                model_metadata=None,
            ),
        ),
        ModelMetadata(request_id=rid, metadata=ResponseModelMetadata()),
        BackendToolCallStarted(request_id=rid, call_id="c", name="n"),
        BackendToolCallCompleted(request_id=rid, call_id="c", name="n", result=None),
    ):
        assert isinstance(ev, SamplingEvent)


# ---------------------------------------------------------------------------
# from_sampling_error: From<&SamplingError> projection (all 11 variants).
# ---------------------------------------------------------------------------


def test_from_auth_error() -> None:
    info = from_sampling_error(Auth("bad token"))
    assert info.kind is SamplingErrorKind.AUTH
    assert info.status_code is None
    assert info.message == "bad token"  # Auth Display is the message verbatim.
    assert info.is_retryable is False
    assert info.retry_after_secs is None
    assert info.model_metadata is None
    assert info.empty_response_context is None
    assert info.doom_loop_triggers is None
    assert info.doom_loop_aborted_at_chunk is None


def test_from_invalid_configuration_maps_to_api_kind() -> None:
    """grok has no ``InvalidConfiguration`` kind -> it maps to ``Api``."""
    info = from_sampling_error(InvalidConfiguration("missing key"))
    assert info.kind is SamplingErrorKind.API
    assert info.message == "invalid client configuration: missing key"
    assert info.is_retryable is False
    assert info.status_code is None


def test_from_serialization_error_prepends_prefix() -> None:
    info = from_sampling_error(Serialization("line 3 column 4: oops"))
    assert info.kind is SamplingErrorKind.SERIALIZATION
    assert info.message == "serialization error: line 3 column 4: oops"
    assert info.is_retryable is False


def test_from_http_error() -> None:
    info = from_sampling_error(Http("connection reset"))
    assert info.kind is SamplingErrorKind.HTTP
    assert info.message == "request error: connection reset"
    assert info.is_retryable is True
    assert info.status_code is None


def test_from_api_500_maps_to_api_kind() -> None:
    info = from_sampling_error(Api(status=500, message="internal error"))
    assert info.kind is SamplingErrorKind.API
    assert info.status_code == 500
    assert info.message == "API error (status 500): internal error"
    assert info.is_retryable is True


def test_from_api_429_maps_to_rate_limited_kind() -> None:
    """``Api`` with status 429 (``is_rate_limited``) -> RATE_LIMITED, and the
    ``retry_after_secs`` / ``model_metadata`` fields propagate."""
    md = ResponseModelMetadata(context_window=8192)
    info = from_sampling_error(
        Api(status=429, message="slow down", retry_after_secs=30, model_metadata=md)
    )
    assert info.kind is SamplingErrorKind.RATE_LIMITED
    assert info.status_code == 429
    assert info.retry_after_secs == 30
    assert info.model_metadata is md
    assert info.is_retryable is True


def test_from_api_400_maps_to_api_kind_not_retryable() -> None:
    info = from_sampling_error(Api(status=400, message="bad request"))
    assert info.kind is SamplingErrorKind.API  # 400 is NOT rate-limited.
    assert info.status_code == 400
    assert info.is_retryable is False


def test_from_event_stream_error_maps_to_http_kind() -> None:
    """``EventStreamError`` -> HTTP kind (transport-class), retryable."""
    info = from_sampling_error(EventStreamError("stream broke"))
    assert info.kind is SamplingErrorKind.HTTP
    assert info.message == "reqwest error stream: stream broke"
    assert info.is_retryable is True


def test_from_stream_error_maps_to_api_kind() -> None:
    info = from_sampling_error(StreamError(error_type="server_error", message="overloaded"))
    assert info.kind is SamplingErrorKind.API
    assert info.message == "stream error (server_error): overloaded"
    assert info.is_retryable is True


def test_from_idle_timeout_not_retryable() -> None:
    info = from_sampling_error(IdleTimeout(elapsed_secs=45))
    assert info.kind is SamplingErrorKind.IDLE_TIMEOUT
    assert info.message == "inference idle timeout after 45s with no chunks"
    assert info.is_retryable is False


def test_from_empty_response_populates_context() -> None:
    """Only :class:`EmptyResponse` fills the ``empty_response_context`` slot."""
    ctx = _empty_ctx()
    info = from_sampling_error(EmptyResponse(context=ctx))
    assert info.kind is SamplingErrorKind.EMPTY_RESPONSE
    assert info.message == "empty response from model (no_visible_content)"
    assert info.is_retryable is True
    assert info.empty_response_context is ctx
    # No doom-loop fields.
    assert info.doom_loop_triggers is None
    assert info.doom_loop_aborted_at_chunk is None


def test_from_max_tokens_truncation_static_message() -> None:
    info = from_sampling_error(MaxTokensTruncation())
    assert info.kind is SamplingErrorKind.MAX_TOKENS_TRUNCATION
    assert info.message == "response truncated by max_tokens"
    assert info.is_retryable is False


def test_from_doom_loop_detected_populates_triggers_and_aborted_chunk() -> None:
    """Only :class:`DoomLoopDetected` fills the doom-loop slots; triggers clone
    to a fresh ``list`` (grok ``triggers.clone()``)."""
    err = DoomLoopDetected(
        triggers=("repetition", "low_entropy"), aborted_at_chunk=99
    )
    info = from_sampling_error(err)
    assert info.kind is SamplingErrorKind.DOOM_LOOP_DETECTED
    assert info.message == "doom loop detected: repetition, low_entropy"
    assert info.is_retryable is True
    assert info.doom_loop_triggers == ["repetition", "low_entropy"]
    assert isinstance(info.doom_loop_triggers, list)
    assert info.doom_loop_aborted_at_chunk == 99


def test_from_doom_loop_detected_aborted_at_chunk_none() -> None:
    """``aborted_at_chunk`` defaults to ``None`` (only seen on completed response)."""
    err = DoomLoopDetected(triggers=("repetition",))
    info = from_sampling_error(err)
    assert info.doom_loop_aborted_at_chunk is None
    assert info.doom_loop_triggers == ["repetition"]
