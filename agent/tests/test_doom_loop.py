"""Tests for sampler.doom_loop (R200, ``xai-grok-sampling-types`` ``doom_loop.rs``).

Covers the migrated tolerant doom-loop wire parsers + policy: the 5 wire
constants + byte-exact fixtures, the 3-variant :class:`DoomLoopSignalKind` +
:class:`DoomLoopPeek` discriminated unions, :meth:`DoomLoopSignal.parse` /
:meth:`DoomLoopSignal.tightest`, :class:`DoomLoopRecoveryPolicy` clamp /
:func:`is_confident` / :func:`confident_triggers` / :func:`from_payload`, and
the :func:`peek_doom_loop` + :func:`is_check_event` free functions.
``serde_json::Value`` -> ``json.loads`` / ``dict.get`` is a pure mapping; no I/O.

Mirrors grok's ``doom_loop.rs`` tests: the 3 parse arms, the grammar-mismatch
``Unknown`` degradation, the default + tolerant policy construction, the
byte-exact sample wire fixtures, the malformed-payload ``CheckEvent`` swallowing,
the confidence conjunction (kind AND channel AND threshold), the
``tightest`` lowest-threshold preference, and the ``is_check_event``
name / payload-type / quoting / garbage matrix.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler.doom_loop import (
    DOOM_LOOP_CHECK_EVENT_TYPE,
    DOOM_LOOP_CHECK_HEADER,
    SAMPLE_CHECK_EVENT_DATA,
    SAMPLE_CHECK_EVENT_DATA_CUMULATIVE,
    THINKING_CHANNEL,
    CheckEvent,
    DoomLoopPeek,
    DoomLoopRecoveryPolicy,
    DoomLoopSignal,
    DoomLoopSignalKind,
    LowLogprob,
    NoDoomLoop,
    ResponseField,
    TailRepetition,
    Unknown,
    is_check_event,
    peek_doom_loop,
)

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_seventeen_symbols() -> None:
    """5 constants/fixtures + 4 DoomLoopSignalKind variants (base + 3) +
    DoomLoopSignal + 4 DoomLoopPeek variants (base + 3) + DoomLoopRecoveryPolicy
    + 2 free functions = 17 re-exported symbols."""
    import minimax_code.sampler.doom_loop as doom_loop

    assert len(doom_loop.__all__) == 17
    assert set(doom_loop.__all__) == {
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
    }


def test_wire_constants_match_server_contract() -> None:
    assert DOOM_LOOP_CHECK_HEADER == "x-grok-doom-loop-check"
    assert DOOM_LOOP_CHECK_EVENT_TYPE == "response.doom_loop_check"
    assert THINKING_CHANNEL == "thinking"
    # Fixtures are byte-exact server wire samples.
    assert "response.doom_loop_check" in SAMPLE_CHECK_EVENT_DATA
    assert "tail_repetition:2@response" in SAMPLE_CHECK_EVENT_DATA_CUMULATIVE


# ---------------------------------------------------------------------------
# DoomLoopSignal.parse: 3 arms + grammar-mismatch tolerance.
# ---------------------------------------------------------------------------


def test_parse_tail_repetition_label() -> None:
    s = DoomLoopSignal.parse("tail_repetition:8@thinking")
    assert isinstance(s.kind, TailRepetition)
    assert s.kind.threshold == 8
    assert s.channel == "thinking"
    assert s.raw == "tail_repetition:8@thinking"


def test_parse_low_logprob_label() -> None:
    s = DoomLoopSignal.parse("low_logprob@response")
    assert isinstance(s.kind, LowLogprob)
    assert s.channel == "response"
    assert s.raw == "low_logprob@response"


def test_parse_unknown_kind_preserved() -> None:
    s = DoomLoopSignal.parse("novel_detector:3@thinking")
    assert isinstance(s.kind, Unknown)
    assert s.kind.kind == "novel_detector:3"
    assert s.channel == "thinking"
    assert s.raw == "novel_detector:3@thinking"


@pytest.mark.parametrize(
    "raw",
    [
        "tail_repetition:huge@thinking",  # non-numeric threshold
        "low_logprob:3@thinking",  # low_logprob must not carry a threshold segment
    ],
)
def test_parse_grammar_mismatches_yield_unknown(raw: str) -> None:
    assert isinstance(DoomLoopSignal.parse(raw).kind, Unknown)


def test_parse_negative_threshold_yields_unknown() -> None:
    """grok ``str::parse::<u32>()`` rejects negatives -> Unknown."""
    assert isinstance(DoomLoopSignal.parse("tail_repetition:-1@thinking").kind, Unknown)


def test_parse_missing_channel_keeps_kind() -> None:
    """Missing ``@channel``: kind still parses, channel empty."""
    s = DoomLoopSignal.parse("tail_repetition:4")
    assert isinstance(s.kind, TailRepetition)
    assert s.kind.threshold == 4
    assert s.channel == ""


def test_parse_empty_label_is_unknown() -> None:
    assert isinstance(DoomLoopSignal.parse("").kind, Unknown)


def test_parse_round_trip_via_raw_is_idempotent() -> None:
    """``parse(s.raw) == s`` -- the verbatim raw label round-trips through parse
    (mirrors grok's serde round-trip; Python has no serde derive)."""
    for raw in (
        "tail_repetition:8@thinking",
        "low_logprob@response",
        "novel_detector:3@thinking",
    ):
        sig = DoomLoopSignal.parse(raw)
        assert DoomLoopSignal.parse(sig.raw) == sig


# ---------------------------------------------------------------------------
# DoomLoopSignal value semantics.
# ---------------------------------------------------------------------------


def test_signal_is_frozen() -> None:
    sig = DoomLoopSignal.parse("tail_repetition:8@thinking")
    with pytest.raises(FrozenInstanceError):
        sig.channel = "response"  # type: ignore[misc]


def test_signal_kind_variants_share_base() -> None:
    """All 3 DoomLoopSignalKind variants are subclasses of the union base."""
    assert isinstance(TailRepetition(8), DoomLoopSignalKind)
    assert isinstance(LowLogprob(), DoomLoopSignalKind)
    assert isinstance(Unknown("novel:1"), DoomLoopSignalKind)


# ---------------------------------------------------------------------------
# DoomLoopRecoveryPolicy: defaults + clamp + tolerance.
# ---------------------------------------------------------------------------


def test_policy_default_matches_documented_tunables() -> None:
    policy = DoomLoopRecoveryPolicy()
    assert policy.max_threshold == DoomLoopRecoveryPolicy.DEFAULT_MAX_THRESHOLD == 8
    assert policy.max_retries == DoomLoopRecoveryPolicy.DEFAULT_MAX_RETRIES == 2


def test_policy_is_frozen() -> None:
    policy = DoomLoopRecoveryPolicy()
    with pytest.raises(FrozenInstanceError):
        policy.max_threshold = 16  # type: ignore[misc]


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, 2),  # below range -> clamped up
        (1, 2),
        (2, 2),  # lower bound inclusive
        (8, 8),  # in range
        (64, 64),  # upper bound inclusive
        (65, 64),  # above range -> clamped down
        (1000, 64),
    ],
)
def test_clamp_max_threshold(value: int, expected: int) -> None:
    assert DoomLoopRecoveryPolicy.clamp_max_threshold(value) == expected


@pytest.mark.parametrize(
    "value, expected",
    [
        (0, 0),  # lower bound inclusive
        (3, 3),
        (5, 5),  # upper bound inclusive
        (6, 5),  # above range -> clamped down
        (-1, 0),  # below range -> clamped up
    ],
)
def test_clamp_max_retries(value: int, expected: int) -> None:
    assert DoomLoopRecoveryPolicy.clamp_max_retries(value) == expected


def test_policy_from_payload_missing_fields_uses_defaults() -> None:
    """serde ``#[serde(default)]``: ``{}`` -> default tunables."""
    assert DoomLoopRecoveryPolicy.from_payload({}) == DoomLoopRecoveryPolicy()


def test_policy_from_payload_partial_and_extra_fields() -> None:
    partial = DoomLoopRecoveryPolicy.from_payload({"max_threshold": 4})
    assert partial.max_threshold == 4
    assert partial.max_retries == DoomLoopRecoveryPolicy.DEFAULT_MAX_RETRIES
    # Unknown keys are ignored (forwards-compat with future configs).
    extra = DoomLoopRecoveryPolicy.from_payload(
        {"max_retries": 1, "future_knob": True}
    )
    assert extra.max_retries == 1
    assert extra.max_threshold == DoomLoopRecoveryPolicy.DEFAULT_MAX_THRESHOLD


# ---------------------------------------------------------------------------
# DoomLoopRecoveryPolicy.is_confident + confident_triggers.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, confident",
    [
        ("tail_repetition:8@thinking", True),  # at threshold, thinking
        ("tail_repetition:2@thinking", True),  # below threshold (tighter)
        ("tail_repetition:9@thinking", False),  # above threshold (looser)
        ("tail_repetition:2@response", False),  # wrong channel
        ("low_logprob@thinking", False),  # wrong kind
        ("novel_detector:2@thinking", False),  # unknown kind
    ],
)
def test_is_confident_conjunction(raw: str, confident: bool) -> None:
    """Confident = TailRepetition AND thinking channel AND threshold <= max."""
    policy = DoomLoopRecoveryPolicy()
    assert policy.is_confident(DoomLoopSignal.parse(raw)) is confident


def test_confident_triggers_filters_to_confident_raws() -> None:
    policy = DoomLoopRecoveryPolicy()
    signals = [
        DoomLoopSignal.parse("tail_repetition:4@response"),
        DoomLoopSignal.parse("tail_repetition:4@thinking"),
        DoomLoopSignal.parse("low_logprob@thinking"),
    ]
    assert policy.confident_triggers(signals) == ["tail_repetition:4@thinking"]
    assert policy.confident_triggers([]) == []


# ---------------------------------------------------------------------------
# DoomLoopSignal.tightest.
# ---------------------------------------------------------------------------


def test_tightest_prefers_lowest_tail_repetition_threshold() -> None:
    assert (
        DoomLoopSignal.tightest([
            "tail_repetition:64@thinking",
            "tail_repetition:4@thinking",
            "tail_repetition:16@thinking",
        ])
        == "tail_repetition:4@thinking"
    )


def test_tightest_mixed_falls_back_to_lowest_tail() -> None:
    assert (
        DoomLoopSignal.tightest(["low_logprob@thinking", "tail_repetition:8@thinking"])
        == "tail_repetition:8@thinking"
    )


def test_tightest_no_tail_falls_back_to_first() -> None:
    """No ``tail_repetition`` label: fall back to the first."""
    assert (
        DoomLoopSignal.tightest(["low_logprob@thinking", "novel:2@thinking"])
        == "low_logprob@thinking"
    )


def test_tightest_empty_is_none() -> None:
    assert DoomLoopSignal.tightest([]) is None


# ---------------------------------------------------------------------------
# DoomLoopPeek union: construction + value semantics.
# ---------------------------------------------------------------------------


def test_peek_variants_construct_and_share_base() -> None:
    sig = (DoomLoopSignal.parse("tail_repetition:8@thinking"),)
    assert isinstance(CheckEvent(signals=sig), CheckEvent)
    assert isinstance(ResponseField(signals=sig), ResponseField)
    none = NoDoomLoop()
    assert isinstance(none, NoDoomLoop)
    # All three share the DoomLoopPeek base.
    assert isinstance(CheckEvent(signals=sig), DoomLoopPeek)
    assert isinstance(ResponseField(signals=sig), DoomLoopPeek)
    assert isinstance(none, DoomLoopPeek)


def test_peek_variants_are_frozen() -> None:
    sig = (DoomLoopSignal.parse("tail_repetition:8@thinking"),)
    event = CheckEvent(signals=sig)
    with pytest.raises(FrozenInstanceError):
        event.signals = ()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# peek_doom_loop: byte-exact fixtures + malformed tolerance.
# ---------------------------------------------------------------------------


def test_peek_sample_wire_frame_first() -> None:
    result = peek_doom_loop(SAMPLE_CHECK_EVENT_DATA)
    assert isinstance(result, CheckEvent)
    assert len(result.signals) == 1
    first = result.signals[0]
    assert isinstance(first.kind, TailRepetition)
    assert first.kind.threshold == 4
    assert first.channel == "response"
    assert first.raw == "tail_repetition:4@response"


def test_peek_sample_wire_frame_cumulative() -> None:
    result = peek_doom_loop(SAMPLE_CHECK_EVENT_DATA_CUMULATIVE)
    assert isinstance(result, CheckEvent)
    assert len(result.signals) == 2
    assert result.signals[0].raw == "tail_repetition:4@response"
    assert isinstance(result.signals[1].kind, TailRepetition)
    assert result.signals[1].kind.threshold == 2


def test_peek_check_event_parses_mixed_triggers() -> None:
    data = (
        '{"type":"response.doom_loop_check","doom_loop_check":'
        '{"triggers":["tail_repetition:8@thinking","low_logprob@thinking"]}}'
    )
    result = peek_doom_loop(data)
    assert isinstance(result, CheckEvent)
    assert len(result.signals) == 2
    assert isinstance(result.signals[0].kind, TailRepetition)
    assert isinstance(result.signals[1].kind, LowLogprob)


@pytest.mark.parametrize(
    "data",
    [
        '{"type":"response.doom_loop_check"}',
        '{"type":"response.doom_loop_check","doom_loop_check":{}}',
        '{"type":"response.doom_loop_check","doom_loop_check":{"triggers":"oops"}}',
        '{"type":"response.doom_loop_check","doom_loop_check":{"triggers":42}}',
        '{"type":"response.doom_loop_check","doom_loop_check":{"triggers":[1,{"a":2}]}}',
        '{"type":"response.doom_loop_check","doom_loop_check":null,"extra":true}',
    ],
)
def test_peek_check_event_swallowed_even_when_malformed(data: str) -> None:
    """The event type alone classifies as CheckEvent so the caller never
    forwards it to the typed parser, whatever the payload -- signals empty."""
    result = peek_doom_loop(data)
    assert isinstance(result, CheckEvent)
    assert result.signals == ()


def test_peek_check_event_skips_non_string_entries() -> None:
    data = (
        '{"type":"response.doom_loop_check","doom_loop_check":'
        '{"triggers":[7,"tail_repetition:8@thinking",null]}}'
    )
    result = peek_doom_loop(data)
    assert isinstance(result, CheckEvent)
    assert len(result.signals) == 1
    assert result.signals[0].raw == "tail_repetition:8@thinking"


def test_peek_terminal_response_field() -> None:
    data = (
        '{"type":"response.completed","response":{"id":"r1",'
        '"doom_loop_check":{"triggers":["tail_repetition:16@thinking"]}}}'
    )
    result = peek_doom_loop(data)
    assert isinstance(result, ResponseField)
    assert len(result.signals) == 1
    assert isinstance(result.signals[0].kind, TailRepetition)
    assert result.signals[0].kind.threshold == 16


@pytest.mark.parametrize(
    "data",
    [
        '{"type":"response.output_text.delta","delta":"hi"}',
        '{"type":"response.completed","response":{"id":"r1"}}',
        "doom_loop_check garbage",  # non-JSON mentioning the key -> NoDoomLoop
        '{"type":"response.output_text.delta","delta":"doom_loop_check"}',
    ],
)
def test_peek_no_doom_loop_for_ordinary_and_unrelated_payloads(data: str) -> None:
    assert peek_doom_loop(data) == NoDoomLoop()


# ---------------------------------------------------------------------------
# is_check_event: name / payload-type / quoting / garbage matrix.
# ---------------------------------------------------------------------------


def test_is_check_event_named_frame_payload_irrelevant() -> None:
    """Named frame: payload validity is irrelevant."""
    assert is_check_event(DOOM_LOOP_CHECK_EVENT_TYPE, "not json") is True


def test_is_check_event_unnamed_frame_by_payload_type() -> None:
    """Unnamed frame identified by its payload ``type`` tag."""
    assert is_check_event("message", SAMPLE_CHECK_EVENT_DATA) is True


def test_is_check_event_quoting_delta_is_not_check() -> None:
    """A normal delta QUOTING the event-type string is not the check event: the
    substring precheck hits but the type confirm fails."""
    quoting = (
        '{"type":"response.output_text.delta","delta":"response.doom_loop_check"}'
    )
    assert is_check_event("response.output_text.delta", quoting) is False


def test_is_check_event_unnamed_unparseable_forwarded() -> None:
    """Unnamed + unparseable payload: forwarded, not swallowed."""
    assert is_check_event("message", "garbage response.doom_loop_check garbage") is False


def test_is_check_event_no_substring_short_circuits() -> None:
    """No mention of the event type -> False without a JSON parse."""
    assert is_check_event("message", '{"type":"response.completed"}') is False
