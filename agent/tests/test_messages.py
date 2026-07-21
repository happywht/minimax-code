"""Tests for sampler.messages stop-reason + usage + delta-body cluster (R201,
``xai-grok-sampling-types`` ``messages.rs``).

Mirrors grok's ``messages.rs`` tests: the ``stop_reason`` catch-all (7 known
values + Unknown preservation + faithful re-serialization + through the
``Option<StopReason>`` field), the refusal ``message_delta`` body parse, and
the refusal ``stop_details`` preservation with unknown keys tolerated. Also
covers Python value semantics (frozen+slots+hashable) and the tolerant
``from_payload`` constructors.

``serde_json::Value`` -> ``dict.get`` is a pure mapping; no I/O.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler.messages import (
    EndTurn,
    MaxTokens,
    MessageDeltaBody,
    MessageDeltaUsage,
    MessagesUsage,
    ModelContextWindowExceeded,
    PauseTurn,
    Refusal,
    StopDetails,
    StopReason,
    StopSequence,
    StreamError,
    ToolUse,
    UnknownStopReason,
    parse_stop_reason,
    stop_reason_to_wire,
)

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_sixteen_symbols() -> None:
    """StopReason union base + 8 variants (7 known + Unknown) + 2 usage types
    + StopDetails + MessageDeltaBody + StreamError + 2 free functions
    (parse_stop_reason + stop_reason_to_wire) = 16 re-exported symbols."""
    import minimax_code.sampler.messages as messages

    assert len(messages.__all__) == 16
    assert set(messages.__all__) == {
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


# ---------------------------------------------------------------------------
# parse_stop_reason: 7 known snake_case values -> variants.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected_cls",
    [
        ("end_turn", EndTurn),
        ("max_tokens", MaxTokens),
        ("tool_use", ToolUse),
        ("stop_sequence", StopSequence),
        ("refusal", Refusal),
        ("pause_turn", PauseTurn),
        ("model_context_window_exceeded", ModelContextWindowExceeded),
    ],
)
def test_parse_stop_reason_known_values_map_to_variants(
    raw: str, expected_cls: type[StopReason]
) -> None:
    """grok's 7 tagged snake_case variants each deserialize to their variant."""
    result = parse_stop_reason(raw)
    assert isinstance(result, expected_cls)
    assert stop_reason_to_wire(result) == raw


def test_parse_stop_reason_unknown_preserves_wire_string() -> None:
    """A future server value falls back to UnknownStopReason, preserving the
    wire string -- mirrors grok's #[serde(untagged)] last-variant catch-all."""
    result = parse_stop_reason("some_future_stop_reason")
    assert isinstance(result, UnknownStopReason)
    assert result.value == "some_future_stop_reason"


def test_unknown_stop_reason_re_serializes_wire_verbatim() -> None:
    """The catch-all must re-serialize the wire string faithfully (grok:
    serde_json::to_string round-trips the unknown value)."""
    assert stop_reason_to_wire(parse_stop_reason("mystery_reason")) == "mystery_reason"


def test_parse_stop_reason_to_wire_round_trip_is_idempotent() -> None:
    """``to_wire(parse(x)) == x`` for known + unknown values (serde round-trip)."""
    for raw in (
        "end_turn",
        "max_tokens",
        "tool_use",
        "stop_sequence",
        "refusal",
        "pause_turn",
        "model_context_window_exceeded",
        "some_future_stop_reason",
    ):
        assert stop_reason_to_wire(parse_stop_reason(raw)) == raw


# ---------------------------------------------------------------------------
# StopReason union value semantics.
# ---------------------------------------------------------------------------


def test_stop_reason_variants_share_base() -> None:
    """All 8 StopReason variants are subclasses of the union base."""
    assert isinstance(EndTurn(), StopReason)
    assert isinstance(MaxTokens(), StopReason)
    assert isinstance(ToolUse(), StopReason)
    assert isinstance(StopSequence(), StopReason)
    assert isinstance(Refusal(), StopReason)
    assert isinstance(PauseTurn(), StopReason)
    assert isinstance(ModelContextWindowExceeded(), StopReason)
    assert isinstance(UnknownStopReason("x"), StopReason)


def test_stop_reason_is_frozen() -> None:
    """``@dataclass(frozen=True)`` -> field mutation raises (mirrors grok's
    immutable enum). ``UnknownStopReason`` carries the only field (``value``)
    in the union; the fieldless variants are frozen by construction (no
    writable slot to mutate)."""
    reason = UnknownStopReason("mystery")
    with pytest.raises(FrozenInstanceError):
        reason.value = "rewritten"  # type: ignore[misc]


def test_known_stop_reason_variants_are_equal_instances() -> None:
    """Known variants carry no fields -> equal within their class + hashable."""
    assert EndTurn() == EndTurn()
    assert hash(EndTurn()) == hash(EndTurn())
    assert Refusal() == Refusal()


def test_unknown_stop_reason_equality_by_value() -> None:
    """UnknownStopReason equality is by the preserved wire value."""
    assert UnknownStopReason("x") == UnknownStopReason("x")
    assert UnknownStopReason("x") != UnknownStopReason("y")
    assert hash(UnknownStopReason("x")) == hash(UnknownStopReason("x"))


# ---------------------------------------------------------------------------
# MessagesUsage: required counters + serde default cache counters.
# ---------------------------------------------------------------------------


def test_messages_usage_defaults_cache_counters_to_zero() -> None:
    """grok #[serde(default)]: missing cache counters -> 0."""
    usage = MessagesUsage(input_tokens=10, output_tokens=20)
    assert usage.input_tokens == 10
    assert usage.output_tokens == 20
    assert usage.cache_creation_input_tokens == 0
    assert usage.cache_read_input_tokens == 0


def test_messages_usage_from_payload_missing_counters_uses_defaults() -> None:
    usage = MessagesUsage.from_payload({"input_tokens": 5, "output_tokens": 7})
    assert usage == MessagesUsage(input_tokens=5, output_tokens=7)


def test_messages_usage_from_payload_full() -> None:
    payload = {
        "input_tokens": 100,
        "output_tokens": 200,
        "cache_creation_input_tokens": 30,
        "cache_read_input_tokens": 40,
    }
    usage = MessagesUsage.from_payload(payload)
    assert usage == MessagesUsage(100, 200, 30, 40)


def test_messages_usage_is_frozen() -> None:
    usage = MessagesUsage(input_tokens=1, output_tokens=2)
    with pytest.raises(FrozenInstanceError):
        usage.input_tokens = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MessageDeltaUsage: only output_tokens required; rest Option<u32>.
# ---------------------------------------------------------------------------


def test_message_delta_usage_defaults_optionals_to_none() -> None:
    """grok #[serde(default)] on Option<u32>: absent counters stay None (not 0)."""
    usage = MessageDeltaUsage(output_tokens=5)
    assert usage.output_tokens == 5
    assert usage.input_tokens is None
    assert usage.cache_read_input_tokens is None
    assert usage.cache_creation_input_tokens is None


def test_message_delta_usage_from_payload_minimal() -> None:
    """The grok test fixture: only output_tokens on the wire."""
    usage = MessageDeltaUsage.from_payload({"output_tokens": 5, "input_tokens": 10})
    assert usage.output_tokens == 5
    assert usage.input_tokens == 10
    assert usage.cache_read_input_tokens is None


def test_message_delta_usage_from_payload_full() -> None:
    payload = {
        "output_tokens": 8,
        "input_tokens": 4,
        "cache_read_input_tokens": 2,
        "cache_creation_input_tokens": 1,
    }
    usage = MessageDeltaUsage.from_payload(payload)
    assert usage == MessageDeltaUsage(8, 4, 2, 1)


# ---------------------------------------------------------------------------
# StopDetails: all-optional + tolerant of unknown keys.
# ---------------------------------------------------------------------------


def test_stop_details_defaults_all_none() -> None:
    """grok #[serde(default)] on every field: {} -> all None."""
    details = StopDetails()
    assert details.type_ is None
    assert details.category is None
    assert details.explanation is None


def test_stop_details_from_payload_preserves_fields_and_ignores_unknowns() -> None:
    """Unknown keys (future_key) are ignored -- forwards-compat with future
    detail shapes (mirrors the grok refusal-stop_details fixture)."""
    details = StopDetails.from_payload(
        {
            "type": "refusal",
            "category": "frontier_llm",
            "explanation": "This request was blocked.",
            "future_key": 42,
        }
    )
    assert details.type_ == "refusal"
    assert details.category == "frontier_llm"
    assert details.explanation == "This request was blocked."


def test_stop_details_is_frozen() -> None:
    details = StopDetails(type_="refusal")
    with pytest.raises(FrozenInstanceError):
        details.type_ = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# MessageDeltaBody: refusal + future stop_reason + stop_details tolerance.
# ---------------------------------------------------------------------------


def test_message_delta_body_refusal_parses_without_details() -> None:
    """grok: refusal message_delta with no stop_details on the wire."""
    body = MessageDeltaBody.from_payload({"stop_reason": "refusal"})
    assert isinstance(body.stop_reason, Refusal)
    assert body.stop_details is None


def test_message_delta_body_future_stop_reason_yields_unknown() -> None:
    """The catch-all must also work through the Option<StopReason> field it is
    parsed from in production (mirrors grok's mystery_reason test)."""
    body = MessageDeltaBody.from_payload({"stop_reason": "mystery_reason"})
    assert isinstance(body.stop_reason, UnknownStopReason)
    assert body.stop_reason.value == "mystery_reason"
    assert body.stop_details is None


def test_message_delta_body_refusal_with_stop_details_preserves_explanation() -> None:
    """grok: refusal message_delta carrying stop_details (Anthropic ToS
    auto-refusal) must preserve the explanation; the trailing stop_sequence
    null + unknown future_key inside stop_details must not fail the parse."""
    body = MessageDeltaBody.from_payload(
        {
            "stop_reason": "refusal",
            "stop_sequence": None,
            "stop_details": {
                "type": "refusal",
                "category": "frontier_llm",
                "explanation": "This request was blocked.",
                "future_key": 42,
            },
        }
    )
    assert isinstance(body.stop_reason, Refusal)
    details = body.stop_details
    assert details is not None
    assert details.type_ == "refusal"
    assert details.category == "frontier_llm"
    assert details.explanation == "This request was blocked."


def test_message_delta_body_empty_payload_yields_none_fields() -> None:
    """An empty delta body -> both fields None (e.g. a mid-stream delta that
    carries no stop info)."""
    body = MessageDeltaBody.from_payload({})
    assert body.stop_reason is None
    assert body.stop_details is None


def test_message_delta_body_non_string_stop_reason_is_ignored() -> None:
    """A malformed (non-string) stop_reason is dropped, not crashed on."""
    body = MessageDeltaBody.from_payload({"stop_reason": 42})
    assert body.stop_reason is None


def test_message_delta_body_non_dict_stop_details_is_ignored() -> None:
    """A malformed (non-dict) stop_details is dropped, not crashed on."""
    body = MessageDeltaBody.from_payload({"stop_details": "oops"})
    assert body.stop_details is None


def test_message_delta_body_is_frozen() -> None:
    body = MessageDeltaBody.from_payload({"stop_reason": "end_turn"})
    with pytest.raises(FrozenInstanceError):
        body.stop_reason = None  # type: ignore[misc]


# ---------------------------------------------------------------------------
# StreamError: tolerant payload construction.
# ---------------------------------------------------------------------------


def test_stream_error_from_payload() -> None:
    error = StreamError.from_payload({"type": "overloaded_error", "message": "Too many"})
    assert error.type_ == "overloaded_error"
    assert error.message == "Too many"


def test_stream_error_missing_fields_default_to_empty() -> None:
    """Strict grok would fail; the platform tolerates a malformed error frame
    rather than crash the stream."""
    error = StreamError.from_payload({})
    assert error.type_ == ""
    assert error.message == ""


def test_stream_error_is_frozen() -> None:
    error = StreamError(type_="x", message="y")
    with pytest.raises(FrozenInstanceError):
        error.message = "z"  # type: ignore[misc]
