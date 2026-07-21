"""Tests for ``sampler.conversation_enums`` (R218, ``xai-grok-sampling-types``
``conversation.rs`` second slice).

Covers the two ``#[serde(other)]`` catch-all wire enums that classify *why* a
conversation item exists -- the user-input semantics axis:

1. :class:`SyntheticReason` (``conversation.rs`` ~75) -- why a ``UserItem`` was
   synthesized by the runtime (12 typed variants + ``UNKNOWN`` catch-all) +
   the pure :meth:`starts_prompt_turn` predicate.
2. :class:`PriorTurnInterrupt` (``conversation.rs`` ~172) -- how the user
   fatally interrupted the preceding turn (3 typed variants + ``UNKNOWN``
   catch-all).

Both carry a *unit* ``#[serde(other)]`` catch-all (``Unknown`` carries no
data, unlike the R201 ``StopReason``'s ``Unknown(String)``). The unit catch-all
maps to an ``UNKNOWN`` StrEnum member whose ``from_payload`` never raises.
"""

from __future__ import annotations

import minimax_code.sampler.conversation_enums as _ce
from minimax_code.sampler import PriorTurnInterrupt, SyntheticReason


class TestBarrelReExport:
    """The sampler barrel re-exports every conversation_enums symbol by
    identity (no accidental shadowing / re-wrapping at the package surface)."""

    def test_barrel_symbols_are_direct_module_references(self) -> None:
        assert PriorTurnInterrupt is _ce.PriorTurnInterrupt
        assert SyntheticReason is _ce.SyntheticReason


# ---------------------------------------------------------------------------
# SyntheticReason: 12 typed + UNKNOWN catch-all StrEnum + starts_prompt_turn.
# ---------------------------------------------------------------------------


class TestSyntheticReasonWireValues:
    """``#[serde(rename_all = "snake_case")]`` -> each member's value is the
    snake_case wire string; ``str(member)`` returns it (StrEnum.__str__)."""

    def test_typed_variants_snake_case_wire_values(self) -> None:
        assert SyntheticReason.COMPACTION_META == "compaction_meta"
        assert SyntheticReason.SYSTEM_REMINDER == "system_reminder"
        assert SyntheticReason.PROJECT_INSTRUCTIONS == "project_instructions"
        assert SyntheticReason.AUTO_CONTINUE == "auto_continue"
        assert SyntheticReason.AUTO_RECOVERY == "auto_recovery"
        assert SyntheticReason.INTERJECTION == "interjection"
        assert SyntheticReason.TASK_COMPLETED == "task_completed"
        assert SyntheticReason.SUBAGENT_COMPLETED == "subagent_completed"
        assert SyntheticReason.NOTIFICATION_DRAIN == "notification_drain"
        assert SyntheticReason.GOAL_SUMMARY == "goal_summary"
        assert SyntheticReason.GOAL_CLASSIFIER_NUDGE == "goal_classifier_nudge"
        assert SyntheticReason.SCHEDULER_FIRED == "scheduler_fired"

    def test_unknown_catch_all_wire_value(self) -> None:
        # The #[serde(other)] unit catch-all -> the "unknown" wire string.
        assert SyntheticReason.UNKNOWN == "unknown"

    def test_str_returns_wire_value(self) -> None:
        # StrEnum.__str__ returns the value -> the wire string round-trips via str().
        assert str(SyntheticReason.TASK_COMPLETED) == "task_completed"
        assert str(SyntheticReason.UNKNOWN) == "unknown"

    def test_member_count(self) -> None:
        # 12 typed variants + 1 UNKNOWN catch-all.
        assert len(SyntheticReason) == 13


class TestSyntheticReasonFromPayload:
    """``from_payload`` mirrors ``#[serde(other)]``: known string -> the
    variant; unknown string -> UNKNOWN; non-string / None -> UNKNOWN. Never
    raises (forward-compat across session-file versions)."""

    def test_known_string_returns_variant(self) -> None:
        assert SyntheticReason.from_payload("task_completed") is SyntheticReason.TASK_COMPLETED
        assert SyntheticReason.from_payload("interjection") is SyntheticReason.INTERJECTION
        assert SyntheticReason.from_payload("scheduler_fired") is SyntheticReason.SCHEDULER_FIRED

    def test_unknown_string_returns_unknown(self) -> None:
        # A reason written by a newer version -> the catch-all, never a raise.
        assert SyntheticReason.from_payload("doom_loop_warning") is SyntheticReason.UNKNOWN
        assert SyntheticReason.from_payload("future_reason_v9") is SyntheticReason.UNKNOWN
        assert SyntheticReason.from_payload("") is SyntheticReason.UNKNOWN

    def test_non_string_returns_unknown(self) -> None:
        # serde would fail on the type mismatch; the platform never crashes.
        assert SyntheticReason.from_payload(None) is SyntheticReason.UNKNOWN
        assert SyntheticReason.from_payload(42) is SyntheticReason.UNKNOWN
        assert SyntheticReason.from_payload(["task_completed"]) is SyntheticReason.UNKNOWN
        assert SyntheticReason.from_payload({}) is SyntheticReason.UNKNOWN


class TestSyntheticReasonStartsPromptTurn:
    """``starts_prompt_turn`` mirrors the grok exhaustive match: the five
    auto-wake reasons (consumed a ``prompt_index`` slot) -> True; every
    mid-turn / synthetic-injection reason + UNKNOWN -> False. ``GOAL_SUMMARY``
    is deliberately False (dual semantics)."""

    def test_auto_wake_reasons_are_true(self) -> None:
        assert SyntheticReason.TASK_COMPLETED.starts_prompt_turn() is True
        assert SyntheticReason.SUBAGENT_COMPLETED.starts_prompt_turn() is True
        assert SyntheticReason.NOTIFICATION_DRAIN.starts_prompt_turn() is True
        assert SyntheticReason.GOAL_CLASSIFIER_NUDGE.starts_prompt_turn() is True
        assert SyntheticReason.SCHEDULER_FIRED.starts_prompt_turn() is True

    def test_mid_turn_and_injection_reasons_are_false(self) -> None:
        assert SyntheticReason.COMPACTION_META.starts_prompt_turn() is False
        assert SyntheticReason.SYSTEM_REMINDER.starts_prompt_turn() is False
        assert SyntheticReason.PROJECT_INSTRUCTIONS.starts_prompt_turn() is False
        assert SyntheticReason.AUTO_CONTINUE.starts_prompt_turn() is False
        assert SyntheticReason.AUTO_RECOVERY.starts_prompt_turn() is False
        assert SyntheticReason.INTERJECTION.starts_prompt_turn() is False

    def test_goal_summary_is_deliberately_false(self) -> None:
        # Tags both the index-consuming turn AND the in-turn directive -> False
        # to avoid over-truncating the common in-turn case.
        assert SyntheticReason.GOAL_SUMMARY.starts_prompt_turn() is False

    def test_unknown_catch_all_is_false(self) -> None:
        assert SyntheticReason.UNKNOWN.starts_prompt_turn() is False

    def test_exhaustive_split(self) -> None:
        # Every member is classified exactly once (exhaustive match parity).
        true_members = {
            SyntheticReason.TASK_COMPLETED,
            SyntheticReason.SUBAGENT_COMPLETED,
            SyntheticReason.NOTIFICATION_DRAIN,
            SyntheticReason.GOAL_CLASSIFIER_NUDGE,
            SyntheticReason.SCHEDULER_FIRED,
        }
        for member in SyntheticReason:
            assert member.starts_prompt_turn() is (member in true_members)


# ---------------------------------------------------------------------------
# PriorTurnInterrupt: 3 typed + UNKNOWN catch-all StrEnum.
# ---------------------------------------------------------------------------


class TestPriorTurnInterruptWireValues:
    """``#[serde(rename_all = "snake_case")]`` -> snake_case wire values."""

    def test_typed_variants_snake_case_wire_values(self) -> None:
        assert PriorTurnInterrupt.MID_TURN_ABORT == "mid_turn_abort"
        assert PriorTurnInterrupt.PERMISSION_REJECTED == "permission_rejected"
        assert PriorTurnInterrupt.PERMISSION_CANCELLED == "permission_cancelled"

    def test_unknown_catch_all_wire_value(self) -> None:
        assert PriorTurnInterrupt.UNKNOWN == "unknown"

    def test_str_returns_wire_value(self) -> None:
        assert str(PriorTurnInterrupt.MID_TURN_ABORT) == "mid_turn_abort"

    def test_member_count(self) -> None:
        # 3 typed variants + 1 UNKNOWN catch-all.
        assert len(PriorTurnInterrupt) == 4


class TestPriorTurnInterruptFromPayload:
    """``from_payload`` mirrors ``#[serde(other)]``: known -> variant; unknown
    string -> UNKNOWN; non-string / None -> UNKNOWN. Never raises."""

    def test_known_string_returns_variant(self) -> None:
        assert (
            PriorTurnInterrupt.from_payload("mid_turn_abort") is PriorTurnInterrupt.MID_TURN_ABORT
        )
        assert (
            PriorTurnInterrupt.from_payload("permission_rejected")
            is PriorTurnInterrupt.PERMISSION_REJECTED
        )
        assert (
            PriorTurnInterrupt.from_payload("permission_cancelled")
            is PriorTurnInterrupt.PERMISSION_CANCELLED
        )

    def test_unknown_string_returns_unknown(self) -> None:
        assert PriorTurnInterrupt.from_payload("future_interrupt_v9") is PriorTurnInterrupt.UNKNOWN
        assert PriorTurnInterrupt.from_payload("") is PriorTurnInterrupt.UNKNOWN

    def test_non_string_returns_unknown(self) -> None:
        assert PriorTurnInterrupt.from_payload(None) is PriorTurnInterrupt.UNKNOWN
        assert PriorTurnInterrupt.from_payload(42) is PriorTurnInterrupt.UNKNOWN
        assert PriorTurnInterrupt.from_payload(["mid_turn_abort"]) is PriorTurnInterrupt.UNKNOWN
