"""Tests for ``sampler.conversation_leaves`` (R217, ``xai-grok-sampling-types``
``conversation.rs`` first slice).

Covers the three zero-dependency pure leaves that open the ``conversation.rs``
migration (the 9481-line API-agnostic conversation-representation mega-module),
landed after the ``types.rs`` 1030-1521 band was exhausted at R216:

1. :func:`reported_cost_ticks` (``conversation.rs`` ~732) -- the
   ``Option<i64>::filter(|&t| t > 0)`` cost-ticks normalizer.
2. :func:`truncate_bytes` (``conversation.rs`` ~1624) -- the UTF-8
   char-boundary-safe byte truncator (the ``is_char_boundary`` walk-back ->
   ``errors="ignore"``).
3. :class:`DanglingToolCallReason` (``conversation.rs`` ~2753) -- the
   "why was this tool call left dangling" tagged union (``#[derive(Debug,
   Clone, Copy)]`` only, NO serde -- a pure in-program enum). Two variants:
   :class:`UserCancelled` (field-less default) + :class:`HarnessHalted`
   (carrying a ``class_`` taxonomy tag, grok ``class`` renamed -- Python hard
   keyword).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import minimax_code.sampler.conversation_leaves as _cl
from minimax_code.sampler import (
    DanglingToolCallReason,
    HarnessHalted,
    UserCancelled,
    reported_cost_ticks,
    truncate_bytes,
)


class TestBarrelReExport:
    """The sampler barrel re-exports every conversation_leaves symbol by
    identity (no accidental shadowing / re-wrapping at the package surface)."""

    def test_barrel_symbols_are_direct_module_references(self) -> None:
        assert DanglingToolCallReason is _cl.DanglingToolCallReason
        assert HarnessHalted is _cl.HarnessHalted
        assert UserCancelled is _cl.UserCancelled
        assert reported_cost_ticks is _cl.reported_cost_ticks
        assert truncate_bytes is _cl.truncate_bytes


# ---------------------------------------------------------------------------
# reported_cost_ticks: Option<i64>::filter(|&t| t > 0).
# ---------------------------------------------------------------------------


class TestReportedCostTicks:
    """``reported_cost_ticks`` mirrors grok ``raw.filter(|&t| t > 0)``: a
    present positive ``int`` -> itself; ``None`` / ``0`` / negative / non-int /
    ``bool`` -> ``None``. The REST layer backfills ``0`` for "unreported cost"
    and negative ticks are never valid, so both collapse to ``None``
    ("unreported", never "free"). ``bool`` is an ``int`` subclass in Python but
    grok ``i64`` rejects a JSON ``true`` -- the explicit exclusion mirrors
    that."""

    def test_positive_int_passes_through(self) -> None:
        assert reported_cost_ticks(1) == 1
        assert reported_cost_ticks(42) == 42
        # A large positive i64 well beyond u32 range (i64 admits up to 2**63-1).
        assert reported_cost_ticks(2 ** 40) == 2 ** 40

    def test_none_is_none(self) -> None:
        assert reported_cost_ticks(None) is None

    def test_zero_is_none(self) -> None:
        # The REST layer backfills 0 for "unreported cost" -> collapses to None.
        assert reported_cost_ticks(0) is None

    def test_negative_is_none(self) -> None:
        # Negative ticks are never valid -> collapses to None.
        assert reported_cost_ticks(-1) is None
        assert reported_cost_ticks(-1000) is None

    def test_bool_is_none(self) -> None:
        # bool is an int subclass but grok i64 rejects a JSON true/false.
        assert reported_cost_ticks(True) is None
        assert reported_cost_ticks(False) is None

    def test_non_int_is_none(self) -> None:
        assert reported_cost_ticks("100") is None
        assert reported_cost_ticks(3.14) is None
        assert reported_cost_ticks([1]) is None
        assert reported_cost_ticks({}) is None


# ---------------------------------------------------------------------------
# truncate_bytes: UTF-8 char-boundary-safe byte truncator.
# ---------------------------------------------------------------------------


class TestTruncateBytes:
    """``truncate_bytes`` mirrors grok ``truncate_bytes``: if the UTF-8 encoding
    is already within ``max_bytes``, return ``s`` unchanged; otherwise cut the
    prefix at ``max_bytes`` bytes and walk back to the last char boundary (a
    multi-byte char split at the cut is dropped -- ``errors="ignore"`` is
    equivalent to the ``is_char_boundary`` walk-back). ``max_bytes <= 0`` /
    non-int / ``bool`` -> the empty string (``usize`` admits no such value)."""

    def test_within_budget_returns_unchanged(self) -> None:
        assert truncate_bytes("hello", 10) == "hello"
        assert truncate_bytes("hello", 5) == "hello"  # exact fit
        assert truncate_bytes("", 5) == ""

    def test_ascii_truncation(self) -> None:
        assert truncate_bytes("hello world", 5) == "hello"

    def test_multibyte_truncation_on_boundary(self) -> None:
        # "你好" is 6 UTF-8 bytes (3 per char); a 3-byte cut lands exactly on
        # the boundary between the two chars -> "你".
        assert truncate_bytes("你好", 3) == "你"
        assert truncate_bytes("你好", 6) == "你好"  # exact fit

    def test_multibyte_split_drops_partial_char(self) -> None:
        # A 4- or 5-byte cut splits the 2nd char -> walk back to the 3-byte
        # boundary (errors="ignore" drops the partial trailing bytes).
        assert truncate_bytes("你好", 4) == "你"
        assert truncate_bytes("你好", 5) == "你"

    def test_emoji_four_byte_char(self) -> None:
        # "a😀" -> 'a' (1 byte) + '😀' (4 bytes); a 2-byte cut splits the emoji
        # -> walk back to the 1-byte boundary -> "a".
        assert truncate_bytes("a😀", 2) == "a"
        assert truncate_bytes("a😀", 5) == "a😀"  # exact fit (1 + 4)

    def test_zero_max_bytes_is_empty(self) -> None:
        assert truncate_bytes("hello", 0) == ""

    def test_negative_max_bytes_is_empty(self) -> None:
        assert truncate_bytes("hello", -1) == ""

    def test_non_int_max_bytes_is_empty(self) -> None:
        assert truncate_bytes("hello", "5") == ""
        assert truncate_bytes("hello", 3.5) == ""

    def test_bool_max_bytes_is_empty(self) -> None:
        # bool is an int subclass but grok usize admits no bool.
        assert truncate_bytes("hello", True) == ""
        assert truncate_bytes("hello", False) == ""


# ---------------------------------------------------------------------------
# DanglingToolCallReason: frozen+slots tagged union (no serde).
# ---------------------------------------------------------------------------


class TestDanglingToolCallReason:
    """``DanglingToolCallReason`` is a frozen+slots tagged union (``#[derive(
    Debug, Clone, Copy)]`` only, NO serde -- a pure in-program enum). The
    ``reason`` parameter of the later :func:`repair_dangling_tool_calls`.
    :class:`UserCancelled` is the field-less default fallback;
    :class:`HarnessHalted` carries a ``class_`` taxonomy tag (grok ``class``,
    renamed -- Python hard keyword, not a Rust one)."""

    def test_variants_construct_and_are_union_members(self) -> None:
        u = UserCancelled()
        h = HarnessHalted(class_="policy_guard")
        assert isinstance(u, DanglingToolCallReason)
        assert isinstance(h, DanglingToolCallReason)

    def test_user_cancelled_is_fieldless(self) -> None:
        # The default fallback carries no data.
        u = UserCancelled()
        assert type(u).__slots__ == ()

    def test_harness_halted_carries_class_tag(self) -> None:
        h = HarnessHalted(class_="internal_error")
        assert h.class_ == "internal_error"
        assert type(h).__slots__ == ("class_",)

    def test_class_round_trip(self) -> None:
        # Every grok call site passes a &'static str taxonomy tag.
        for tag in ["policy_guard", "internal_error", "timeout", "oom"]:
            assert HarnessHalted(class_=tag).class_ == tag

    def test_frozen_mutation_raises(self) -> None:
        # Variable field name (not a constant) -- avoids the B010 rule.
        h = HarnessHalted(class_="x")
        field_name = next(iter(type(h).__slots__))
        with pytest.raises(FrozenInstanceError):
            setattr(h, field_name, "mutated")

    def test_no_instance_dict(self) -> None:
        # slots=True -> instances carry no per-instance __dict__.
        assert not hasattr(UserCancelled(), "__dict__")
        assert not hasattr(HarnessHalted(class_="x"), "__dict__")

    def test_hashable(self) -> None:
        # frozen=True restores __hash__; both variants + the class_ str are hashable.
        assert hash(UserCancelled()) == hash(UserCancelled())
        assert hash(HarnessHalted(class_="x")) == hash(HarnessHalted(class_="x"))

    def test_equality_within_variant(self) -> None:
        assert UserCancelled() == UserCancelled()
        assert HarnessHalted(class_="x") == HarnessHalted(class_="x")
        assert HarnessHalted(class_="x") != HarnessHalted(class_="y")

    def test_distinct_variants_are_unequal(self) -> None:
        # The two variants are distinct types -- cross-variant equality is False.
        assert type(UserCancelled()) is UserCancelled
        assert type(HarnessHalted(class_="x")) is HarnessHalted
        assert UserCancelled is not HarnessHalted
        assert UserCancelled() != HarnessHalted(class_="x")
