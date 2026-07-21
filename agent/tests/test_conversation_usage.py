"""Tests for ``sampler.conversation_usage`` (R219, ``xai-grok-sampling-types``
``conversation.rs`` third slice).

Covers the deferred "response stop + usage" cluster first noted in R217
:mod:`conversation_leaves` -- the four symbols that land together once grok
``Usage`` migrates (R207, as :class:`ChatUsage`):

1. :class:`ConversationStopReason` (``conversation.rs`` ~606) -- the strict
   ``snake_case`` 4-variant StrEnum (renamed to dodge the R201
   ``messages.StopReason`` barrel collision). NO ``#[serde(other)]`` catch-all
   -> unknown wire string raises (parity with :class:`FinishReason`), so the
   strict :func:`_parse_strict_enum` path applies -- contrast the R218
   ``UNKNOWN`` catch-all enums which never raise.
2. :class:`TokenUsage` (``conversation.rs`` ~644) -- the flat 5x ``u32``
   conversation-side token counter (frozen+slots dataclass). Distinct from
   :class:`ChatUsage` (the wire shape); built from one via :func:`from_usage`.
3. :func:`from_finish_reason` (``impl From<FinishReason> for StopReason``,
   ~629) -- the finish-reason projection. ``ToolCalls`` and ``FunctionCall``
   both collapse to ``ToolCalls``.
4. :func:`from_usage` (``impl From<Usage> for TokenUsage``, ~667) -- the usage
   projection. ``cached_prompt_tokens`` from ``prompt_tokens_details``;
   ``reasoning_tokens`` from ``completion_tokens_details``; both default to 0
   when the breakdown is absent.
"""

from __future__ import annotations

import pytest

import minimax_code.sampler.conversation_usage as _cu
from minimax_code.sampler import (
    ChatUsage,
    CompletionTokensDetails,
    ConversationStopReason,
    FinishReason,
    PromptTokensDetails,
    TokenUsage,
    from_finish_reason,
    from_usage,
)


class TestBarrelReExport:
    """The sampler barrel re-exports every conversation_usage symbol by
    identity (no accidental shadowing / re-wrapping at the package surface)."""

    def test_barrel_symbols_are_direct_module_references(self) -> None:
        assert ConversationStopReason is _cu.ConversationStopReason
        assert TokenUsage is _cu.TokenUsage
        assert from_finish_reason is _cu.from_finish_reason
        assert from_usage is _cu.from_usage


# ---------------------------------------------------------------------------
# ConversationStopReason: strict 4-variant snake_case StrEnum (no catch-all).
# ---------------------------------------------------------------------------


class TestConversationStopReasonWireValues:
    """``#[serde(rename_all = "snake_case")]`` -> each member's value is the
    snake_case wire string; ``str(member)`` returns it (StrEnum.__str__)."""

    def test_variants_snake_case_wire_values(self) -> None:
        assert ConversationStopReason.STOP == "stop"
        assert ConversationStopReason.LENGTH == "length"
        assert ConversationStopReason.TOOL_CALLS == "tool_calls"
        assert ConversationStopReason.CONTENT_FILTER == "content_filter"

    def test_str_returns_wire_value(self) -> None:
        # StrEnum.__str__ returns the value -> the wire string round-trips.
        assert str(ConversationStopReason.STOP) == "stop"
        assert str(ConversationStopReason.CONTENT_FILTER) == "content_filter"

    def test_member_count(self) -> None:
        # Exactly the 4 typed variants -- no UNKNOWN catch-all (strict enum).
        assert len(ConversationStopReason) == 4

    def test_no_unknown_member(self) -> None:
        # The #[serde(other)]-less strict enum has no UNKNOWN escape hatch.
        assert not hasattr(ConversationStopReason, "UNKNOWN")


class TestConversationStopReasonAsStr:
    """``as_str`` mirrors the grok method: returns the snake_case wire string
    (same value as ``str(member)`` for a StrEnum, but the method exists to
    mirror the grok surface verbatim)."""

    def test_as_str_returns_wire_value_for_every_member(self) -> None:
        assert ConversationStopReason.STOP.as_str() == "stop"
        assert ConversationStopReason.LENGTH.as_str() == "length"
        assert ConversationStopReason.TOOL_CALLS.as_str() == "tool_calls"
        assert ConversationStopReason.CONTENT_FILTER.as_str() == "content_filter"

    def test_as_str_matches_str(self) -> None:
        for member in ConversationStopReason:
            assert member.as_str() == str(member)


class TestConversationStopReasonFromPayload:
    """``from_payload`` is strict (no catch-all): a known wire string -> the
    variant; an unknown wire string -> ``ValueError``; a non-string ->
    ``ValueError``. Mirrors serde's enum failure on this
    ``#[serde(other)]``-less enum (contrast the R218 UNKNOWN catch-all which
    never raises)."""

    def test_known_string_returns_variant(self) -> None:
        assert ConversationStopReason.from_payload("stop") is ConversationStopReason.STOP
        assert (
            ConversationStopReason.from_payload("length") is ConversationStopReason.LENGTH
        )
        assert (
            ConversationStopReason.from_payload("tool_calls")
            is ConversationStopReason.TOOL_CALLS
        )
        assert (
            ConversationStopReason.from_payload("content_filter")
            is ConversationStopReason.CONTENT_FILTER
        )

    def test_unknown_string_raises(self) -> None:
        # A reason written by a newer version raises (no catch-all) -- the
        # caller must decide how to handle the forward-compat gap.
        with pytest.raises(ValueError):
            ConversationStopReason.from_payload("future_stop_v9")
        with pytest.raises(ValueError):
            ConversationStopReason.from_payload("")

    def test_non_string_raises(self) -> None:
        # serde would fail on the type mismatch; the strict parser surfaces it.
        with pytest.raises(ValueError):
            ConversationStopReason.from_payload(None)
        with pytest.raises(ValueError):
            ConversationStopReason.from_payload(42)
        with pytest.raises(ValueError):
            ConversationStopReason.from_payload(["stop"])
        with pytest.raises(ValueError):
            ConversationStopReason.from_payload({})

    def test_exhaustive_known_strings(self) -> None:
        # Every member round-trips through its own wire value.
        for member in ConversationStopReason:
            assert ConversationStopReason.from_payload(member.value) is member


# ---------------------------------------------------------------------------
# from_finish_reason: FinishReason -> ConversationStopReason projection.
# ---------------------------------------------------------------------------


class TestFromFinishReason:
    """``impl From<FinishReason> for StopReason``: 1:1 for Stop / Length /
    ContentFilter; both ``ToolCalls`` and ``FunctionCall`` collapse to
    ``ToolCalls`` (the legacy function-call finish is observed as a tool-call
    stop on the conversation side)."""

    def test_one_to_one_variants(self) -> None:
        assert from_finish_reason(FinishReason.STOP) is ConversationStopReason.STOP
        assert from_finish_reason(FinishReason.LENGTH) is ConversationStopReason.LENGTH
        assert (
            from_finish_reason(FinishReason.CONTENT_FILTER)
            is ConversationStopReason.CONTENT_FILTER
        )

    def test_tool_calls_passes_through(self) -> None:
        assert (
            from_finish_reason(FinishReason.TOOL_CALLS)
            is ConversationStopReason.TOOL_CALLS
        )

    def test_function_call_collapses_to_tool_calls(self) -> None:
        # The legacy function-call finish is observed as a tool-call stop on
        # the conversation side (grok folds both arms into one).
        assert (
            from_finish_reason(FinishReason.FUNCTION_CALL)
            is ConversationStopReason.TOOL_CALLS
        )

    def test_exhaustive_mapping(self) -> None:
        # Every FinishReason member maps exactly once; the collapse is the only
        # non-1:1 arm.
        expected = {
            FinishReason.STOP: ConversationStopReason.STOP,
            FinishReason.LENGTH: ConversationStopReason.LENGTH,
            FinishReason.TOOL_CALLS: ConversationStopReason.TOOL_CALLS,
            FinishReason.FUNCTION_CALL: ConversationStopReason.TOOL_CALLS,
            FinishReason.CONTENT_FILTER: ConversationStopReason.CONTENT_FILTER,
        }
        for fr in FinishReason:
            assert from_finish_reason(fr) is expected[fr]

    def test_image_is_conversation_stop_reason(self) -> None:
        # The projection always lands in the conversation enum, never leaks the
        # input FinishReason type through.
        for fr in FinishReason:
            assert isinstance(from_finish_reason(fr), ConversationStopReason)


# ---------------------------------------------------------------------------
# TokenUsage: flat 5x u32 frozen+slots dataclass.
# ---------------------------------------------------------------------------


class TestTokenUsageDefaults:
    """``#[derive(Default)]`` -> every counter defaults to 0; ``default()``
    returns the all-zero instance."""

    def test_default_construction_is_all_zero(self) -> None:
        usage = TokenUsage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0
        assert usage.reasoning_tokens == 0
        assert usage.cached_prompt_tokens == 0

    def test_default_classmethod_matches_zero_constructor(self) -> None:
        assert TokenUsage.default() == TokenUsage()

    def test_field_construction_preserves_values(self) -> None:
        usage = TokenUsage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            reasoning_tokens=20,
            cached_prompt_tokens=30,
        )
        assert usage.prompt_tokens == 100
        assert usage.completion_tokens == 50
        assert usage.total_tokens == 150
        assert usage.reasoning_tokens == 20
        assert usage.cached_prompt_tokens == 30

    def test_equality_is_structural(self) -> None:
        # frozen dataclass -> value equality, not identity.
        assert TokenUsage(prompt_tokens=1) == TokenUsage(prompt_tokens=1)
        assert TokenUsage(prompt_tokens=1) != TokenUsage(prompt_tokens=2)


class TestTokenUsageSlots:
    """frozen+slots -> the dataclass has ``__slots__`` with exactly the five
    counter fields and is immutable after construction."""

    def test_uses_slots_with_five_counter_fields(self) -> None:
        slots = type(TokenUsage()).__slots__
        assert set(slots) == {
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "reasoning_tokens",
            "cached_prompt_tokens",
        }

    def test_no_dict_attribute(self) -> None:
        # slots -> no __dict__ on the instance (memory + the immutability
        # surface is closed).
        assert not hasattr(TokenUsage(), "__dict__")

    def test_frozen_immutable(self) -> None:
        usage = TokenUsage(prompt_tokens=1)
        # The first real slot field (variable field name keeps the test honest
        # if the field order ever changes).
        field = next(iter(type(usage).__slots__))
        with pytest.raises(AttributeError):
            setattr(usage, field, 999)


class TestTokenUsageFromPayload:
    """``from_payload`` is tolerant: each counter defaults to 0 when the
    payload is missing / null / not a dict / lacks the key (mirrors grok's
    ``#[serde(default)]`` on ``cached_prompt_tokens``, extended uniformly).
    Parity with :meth:`ChatUsage.from_payload`."""

    def test_full_dict_round_trips(self) -> None:
        usage = TokenUsage.from_payload(
            {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
                "reasoning_tokens": 20,
                "cached_prompt_tokens": 30,
            }
        )
        assert usage == TokenUsage(100, 50, 150, 20, 30)

    def test_non_dict_returns_default(self) -> None:
        # A non-dict payload is the all-zero default (tolerant edge).
        assert TokenUsage.from_payload(None) == TokenUsage()
        assert TokenUsage.from_payload(42) == TokenUsage()
        assert TokenUsage.from_payload("not-a-dict") == TokenUsage()
        assert TokenUsage.from_payload([]) == TokenUsage()

    def test_missing_keys_default_to_zero(self) -> None:
        usage = TokenUsage.from_payload({"prompt_tokens": 7})
        assert usage.prompt_tokens == 7
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0
        assert usage.reasoning_tokens == 0
        assert usage.cached_prompt_tokens == 0

    def test_empty_dict_returns_default(self) -> None:
        assert TokenUsage.from_payload({}) == TokenUsage()

    def test_cached_prompt_tokens_optional(self) -> None:
        # grok marks only cached_prompt_tokens #[serde(default)]; the platform
        # extends that tolerance uniformly -- but the salient case (cached
        # absent) still lands 0.
        usage = TokenUsage.from_payload(
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        )
        assert usage.cached_prompt_tokens == 0
        assert usage.reasoning_tokens == 0


# ---------------------------------------------------------------------------
# from_usage: ChatUsage -> TokenUsage projection.
# ---------------------------------------------------------------------------


class TestFromUsage:
    """``impl From<Usage> for TokenUsage``: bare counters pass straight
    through; ``cached_prompt_tokens`` is pulled from
    ``prompt_tokens_details.cached_tokens`` (0 when absent);
    ``reasoning_tokens`` from ``completion_tokens_details.reasoning_tokens``
    (0 when absent); the cost extension and non-salient breakdown fields are
    dropped."""

    def test_full_projection_with_both_details(self) -> None:
        usage = ChatUsage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            prompt_tokens_details=PromptTokensDetails(cached_tokens=30),
            completion_tokens_details=CompletionTokensDetails(reasoning_tokens=20),
        )
        result = from_usage(usage)
        assert result == TokenUsage(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            reasoning_tokens=20,
            cached_prompt_tokens=30,
        )

    def test_passes_through_bare_counters(self) -> None:
        usage = ChatUsage(prompt_tokens=42, completion_tokens=17, total_tokens=59)
        result = from_usage(usage)
        assert result.prompt_tokens == 42
        assert result.completion_tokens == 17
        assert result.total_tokens == 59

    def test_missing_details_default_to_zero(self) -> None:
        # Both breakdowns absent -> both projected fields 0 (grok's map_or(0, ..)).
        usage = ChatUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        result = from_usage(usage)
        assert result.cached_prompt_tokens == 0
        assert result.reasoning_tokens == 0

    def test_only_prompt_tokens_details(self) -> None:
        usage = ChatUsage(
            prompt_tokens=10,
            prompt_tokens_details=PromptTokensDetails(cached_tokens=7),
        )
        result = from_usage(usage)
        assert result.cached_prompt_tokens == 7
        assert result.reasoning_tokens == 0

    def test_only_completion_tokens_details(self) -> None:
        usage = ChatUsage(
            completion_tokens=20,
            completion_tokens_details=CompletionTokensDetails(reasoning_tokens=9),
        )
        result = from_usage(usage)
        assert result.reasoning_tokens == 9
        assert result.cached_prompt_tokens == 0

    def test_zero_valued_detail_fields_propagate(self) -> None:
        # An explicit 0 in the breakdown is preserved (not just an absent one).
        usage = ChatUsage(
            prompt_tokens_details=PromptTokensDetails(cached_tokens=0),
            completion_tokens_details=CompletionTokensDetails(reasoning_tokens=0),
        )
        result = from_usage(usage)
        assert result.cached_prompt_tokens == 0
        assert result.reasoning_tokens == 0

    def test_drops_cost_extension(self) -> None:
        # cost_in_usd_ticks is the xAI billing extension; it has no slot on
        # TokenUsage and is dropped at this projection.
        usage = ChatUsage(prompt_tokens=1, cost_in_usd_ticks=1234567890)
        result = from_usage(usage)
        assert not hasattr(result, "cost_in_usd_ticks")

    def test_returns_token_usage_type(self) -> None:
        # The projection always lands in TokenUsage, never leaks the ChatUsage
        # input type through.
        result = from_usage(ChatUsage())
        assert isinstance(result, TokenUsage)
