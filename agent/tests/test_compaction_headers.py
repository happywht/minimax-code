"""Tests for sampler.compaction_headers (R208,
``xai-grok-sampling-types`` ``types.rs``).

Covers the third ``types.rs`` slice -- 2 zero-dependency ``#[serde(untagged)]``
newtype-style decision enums that drive the ``x-compactions-*`` request headers,
each with a ``resolve()`` decision method:

- :class:`CompactionAtTokens` (untagged bool/int union + resolve) -- the
  ``x-compactions-at-tokens`` header knob. ``Enabled(bool)`` toggles the
  auto-computed value (``context_window * threshold_percent // 100``);
  ``Fixed(u64)`` sends a constant.
- :class:`CompactionsRemaining` (untagged bool/int union + resolve) -- the
  ``x-compactions-remaining`` header knob. ``Dynamic(bool)`` toggles the
  dynamic value (``1`` on the uncompacted prefix, ``0`` once a compaction
  summary exists); ``Fixed(u8)`` sends a constant.

The two enums close against the std scalars alone (no R206/R207 leaf, no
``crate::rs``, no ``serde_helpers``). The critical migration invariant tested
below is the **bool-before-int untagged parse guard**: ``isinstance(payload,
bool)`` MUST be tested before ``isinstance(payload, int)`` -- ``bool`` subclasses
``int`` in Python, so ``isinstance(True, int)`` is ``True``, and without the
guard ``True`` / ``False`` would mis-parse as ``Fixed(1)`` / ``Fixed(0)``.

The ``resolve()`` integer division mirrors grok's unsigned-integer arithmetic
(``context_window * threshold_percent // 100``); the ``!has_compaction_summary``
bool-to-u8 coercion mirrors ``u8::from(!bool)`` (``True`` -> 0, ``False`` -> 1).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler.compaction_headers import (
    CompactionAtTokens,
    CompactionAtTokensEnabled,
    CompactionAtTokensFixed,
    CompactionsRemaining,
    CompactionsRemainingDynamic,
    CompactionsRemainingFixed,
)

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_six_symbols() -> None:
    """2 union bases + 4 variant subclasses = 6 re-exported symbols."""
    import minimax_code.sampler.compaction_headers as compaction

    assert len(compaction.__all__) == 6
    assert set(compaction.__all__) == {
        "CompactionAtTokens",
        "CompactionAtTokensEnabled",
        "CompactionAtTokensFixed",
        "CompactionsRemaining",
        "CompactionsRemainingDynamic",
        "CompactionsRemainingFixed",
    }


def test_package_barrel_re_exports_compaction_symbols() -> None:
    """The package barrel flattens the 6 compaction decision symbols."""
    import minimax_code.sampler as sampler

    for name in (
        "CompactionAtTokens",
        "CompactionAtTokensEnabled",
        "CompactionAtTokensFixed",
        "CompactionsRemaining",
        "CompactionsRemainingDynamic",
        "CompactionsRemainingFixed",
    ):
        assert name in sampler.__all__


# ---------------------------------------------------------------------------
# CompactionAtTokens.from_payload: bool-before-int untagged parse (CRITICAL).
# ---------------------------------------------------------------------------


def test_compaction_at_tokens_true_is_enabled_not_fixed_one() -> None:
    """CRITICAL bool-before-int guard: ``True`` parses as ``Enabled(True)``,
    NOT ``Fixed(1)``. ``bool`` subclasses ``int`` in Python, so without the guard
    ``isinstance(True, int)`` would mis-match ``Fixed(u64)``."""
    result = CompactionAtTokens.from_payload(True)
    assert isinstance(result, CompactionAtTokensEnabled)
    assert not isinstance(result, CompactionAtTokensFixed)
    assert result.enabled is True


def test_compaction_at_tokens_false_is_enabled_not_fixed_zero() -> None:
    """``False`` parses as ``Enabled(False)``, NOT ``Fixed(0)``."""
    result = CompactionAtTokens.from_payload(False)
    assert isinstance(result, CompactionAtTokensEnabled)
    assert result.enabled is False


def test_compaction_at_tokens_integer_is_fixed() -> None:
    result = CompactionAtTokens.from_payload(4096)
    assert isinstance(result, CompactionAtTokensFixed)
    assert result.value == 4096


def test_compaction_at_tokens_zero_is_fixed_zero() -> None:
    """``0`` is an int (not a bool) -> ``Fixed(0)`` (contrast ``False`` ->
    ``Enabled(False)``)."""
    result = CompactionAtTokens.from_payload(0)
    assert isinstance(result, CompactionAtTokensFixed)
    assert result.value == 0


@pytest.mark.parametrize("payload", ["auto", None, 1.5, [True], {}])
def test_compaction_at_tokens_rejects_non_bool_int(payload: object) -> None:
    """Any non-bool / non-int wire value raises ``ValueError`` (mirrors serde's
    untagged failure -- no catch-all). Note ``1.5`` (float) is rejected: grok's
    ``Fixed(u64)`` takes an integer, not a float."""
    with pytest.raises(ValueError):
        CompactionAtTokens.from_payload(payload)


# ---------------------------------------------------------------------------
# CompactionAtTokens.resolve: the absolute token count decision.
# ---------------------------------------------------------------------------


def test_compaction_at_tokens_resolve_enabled_true_auto_computes() -> None:
    """``Enabled(true)`` -> ``context_window * threshold_percent // 100``
    (unsigned-integer arithmetic, mirroring grok's ``u64`` division)."""
    assert CompactionAtTokensEnabled(enabled=True).resolve(1000, 25) == 250


def test_compaction_at_tokens_resolve_integer_division_truncates() -> None:
    """33% of 1000 = 330 (integer division of 1000 * 33 = 33000 // 100)."""
    assert CompactionAtTokensEnabled(enabled=True).resolve(1000, 33) == 330
    # 1000 * 7 = 7000 // 100 = 70 (truncated, not 70.0)
    assert CompactionAtTokensEnabled(enabled=True).resolve(1000, 7) == 70


def test_compaction_at_tokens_resolve_enabled_false_is_none() -> None:
    """``Enabled(false)`` -> ``None`` (header disabled)."""
    assert CompactionAtTokensEnabled(enabled=False).resolve(1000, 25) is None


def test_compaction_at_tokens_resolve_fixed_is_value_ignoring_args() -> None:
    """``Fixed(n)`` -> ``n`` (ignores ``context_window`` / ``threshold_percent``)."""
    assert CompactionAtTokensFixed(value=4096).resolve(1000, 25) == 4096
    assert CompactionAtTokensFixed(value=4096).resolve(0, 0) == 4096


def test_compaction_at_tokens_from_payload_resolves_through_chain() -> None:
    """``from_payload`` -> ``resolve`` end-to-end."""
    assert CompactionAtTokens.from_payload(True).resolve(800, 50) == 400
    assert CompactionAtTokens.from_payload(False).resolve(800, 50) is None
    assert CompactionAtTokens.from_payload(5000).resolve(800, 50) == 5000


# ---------------------------------------------------------------------------
# CompactionsRemaining.from_payload: same bool-before-int guard.
# ---------------------------------------------------------------------------


def test_compactions_remaining_true_is_dynamic_not_fixed_one() -> None:
    """Bool-before-int guard: ``True`` -> ``Dynamic(True)``, NOT ``Fixed(1)``."""
    result = CompactionsRemaining.from_payload(True)
    assert isinstance(result, CompactionsRemainingDynamic)
    assert not isinstance(result, CompactionsRemainingFixed)
    assert result.dynamic is True


def test_compactions_remaining_false_is_dynamic_not_fixed_zero() -> None:
    result = CompactionsRemaining.from_payload(False)
    assert isinstance(result, CompactionsRemainingDynamic)
    assert result.dynamic is False


def test_compactions_remaining_integer_is_fixed() -> None:
    result = CompactionsRemaining.from_payload(3)
    assert isinstance(result, CompactionsRemainingFixed)
    assert result.value == 3


@pytest.mark.parametrize("payload", ["auto", None, 1.5, [True], {}])
def test_compactions_remaining_rejects_non_bool_int(payload: object) -> None:
    with pytest.raises(ValueError):
        CompactionsRemaining.from_payload(payload)


# ---------------------------------------------------------------------------
# CompactionsRemaining.resolve: the header value decision.
# ---------------------------------------------------------------------------


def test_compactions_remaining_resolve_dynamic_true_no_summary_is_one() -> None:
    """``Dynamic(true)`` on the uncompacted prefix -> ``1`` (one compaction
    remaining). Mirrors ``u8::from(!has_compaction_summary)`` = ``!False`` = 1."""
    assert CompactionsRemainingDynamic(dynamic=True).resolve(False) == 1


def test_compactions_remaining_resolve_dynamic_true_with_summary_is_zero() -> None:
    """``Dynamic(true)`` once the session has a compaction summary -> ``0``.
    Mirrors ``u8::from(!has_compaction_summary)`` = ``!True`` = 0."""
    assert CompactionsRemainingDynamic(dynamic=True).resolve(True) == 0


def test_compactions_remaining_resolve_dynamic_false_is_none() -> None:
    """``Dynamic(false)`` -> ``None`` (header disabled)."""
    assert CompactionsRemainingDynamic(dynamic=False).resolve(False) is None
    assert CompactionsRemainingDynamic(dynamic=False).resolve(True) is None


def test_compactions_remaining_resolve_fixed_is_value_ignoring_summary() -> None:
    """``Fixed(n)`` -> ``n`` (ignores ``has_compaction_summary``)."""
    assert CompactionsRemainingFixed(value=3).resolve(False) == 3
    assert CompactionsRemainingFixed(value=3).resolve(True) == 3


def test_compactions_remaining_from_payload_resolves_through_chain() -> None:
    assert CompactionsRemaining.from_payload(True).resolve(False) == 1
    assert CompactionsRemaining.from_payload(True).resolve(True) == 0
    assert CompactionsRemaining.from_payload(False).resolve(False) is None
    assert CompactionsRemaining.from_payload(2).resolve(True) == 2


# ---------------------------------------------------------------------------
# Union subclassing: variants ARE the union base.
# ---------------------------------------------------------------------------


def test_compaction_variants_are_subclasses_of_union_base() -> None:
    """``isinstance`` dispatch in ``resolve`` relies on the variant subclasses
    being subclasses of the union base."""
    assert issubclass(CompactionAtTokensEnabled, CompactionAtTokens)
    assert issubclass(CompactionAtTokensFixed, CompactionAtTokens)
    assert issubclass(CompactionsRemainingDynamic, CompactionsRemaining)
    assert issubclass(CompactionsRemainingFixed, CompactionsRemaining)


def test_from_payload_returns_subclass_instances() -> None:
    assert isinstance(CompactionAtTokens.from_payload(True), CompactionAtTokens)
    assert isinstance(CompactionAtTokens.from_payload(5), CompactionAtTokens)
    assert isinstance(CompactionsRemaining.from_payload(False), CompactionsRemaining)
    assert isinstance(CompactionsRemaining.from_payload(1), CompactionsRemaining)


# ---------------------------------------------------------------------------
# Value semantics: frozen + slots + hashable.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obj",
    [
        CompactionAtTokensEnabled(enabled=True),
        CompactionAtTokensFixed(value=4096),
        CompactionsRemainingDynamic(dynamic=False),
        CompactionsRemainingFixed(value=2),
    ],
)
def test_compaction_variants_are_frozen(obj: object) -> None:
    """``@dataclass(frozen=True)`` -> mutating the first declared slot raises
    (B010-safe: the attribute name is a variable, not a literal constant)."""
    field_name = next(iter(type(obj).__slots__))
    with pytest.raises(FrozenInstanceError):
        setattr(obj, field_name, "rewritten")  # type: ignore[misc]


def test_compaction_variants_are_hashable_and_equal() -> None:
    """``frozen=True`` -> hashable + equal-by-value."""
    assert CompactionAtTokensEnabled(enabled=True) == CompactionAtTokensEnabled(enabled=True)
    assert hash(CompactionAtTokensEnabled(enabled=True)) == hash(
        CompactionAtTokensEnabled(enabled=True)
    )
    assert CompactionAtTokensFixed(value=5) == CompactionAtTokensFixed(value=5)
    assert hash(CompactionAtTokensFixed(value=5)) == hash(CompactionAtTokensFixed(value=5))
    assert CompactionsRemainingDynamic(dynamic=True) == CompactionsRemainingDynamic(dynamic=True)
    assert CompactionsRemainingFixed(value=1) == CompactionsRemainingFixed(value=1)


def test_compaction_variants_declare_slots() -> None:
    """``slots=True`` -> each variant declares ``__slots__`` over its field."""
    assert CompactionAtTokensEnabled.__slots__ == ("enabled",)
    assert CompactionAtTokensFixed.__slots__ == ("value",)
    assert CompactionsRemainingDynamic.__slots__ == ("dynamic",)
    assert CompactionsRemainingFixed.__slots__ == ("value",)
