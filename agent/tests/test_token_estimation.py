"""Tests for the pure token-estimation primitives (R27).

Mirrors grok-build's ``xai-token-estimation`` test module one-for-one —
every Rust ``#[test]`` becomes a Python test with the same inputs and
expected outputs — then adds Python-specific guard tests that lock the
two parity traps documented in :mod:`minimax_code.token_estimation`:

* the round-half-up contract (vs Python's banker's-rounding ``round``), and
* the saturating-subtraction contract in
  :func:`~minimax_code.token_estimation.exceeds_threshold_with_headroom`.

The whole module is pure arithmetic, so every test is a plain assertion
over literal inputs — no fixtures, no async, no IO.
"""

from __future__ import annotations

import pytest

from minimax_code.token_estimation import (
    BYTES_PER_TOKEN,
    IMAGE_TOKEN_ESTIMATE,
    estimate_chars,
    estimate_image_tokens,
    estimate_tokens,
    exceeds_threshold,
    exceeds_threshold_with_headroom,
    free_tokens,
    usage_percentage,
    usage_percentage_truncated_u8,
    usage_percentage_u8,
)

# Mirror of Rust's u64::MAX — used by the saturating-overflow guard test.
_U64_MAX = 18446744073709551615


# -- estimate_tokens --------------------------------------------------------


def test_estimate_tokens_is_bytes_over_four():
    """len(s) // 4 — empty and sub-token strings round down to zero."""
    assert estimate_tokens("") == 0
    assert estimate_tokens("abc") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("x" * 4000) == 1000


def test_estimate_tokens_uses_bytes_not_codepoints():
    """Rust ``&str::len`` counts UTF-8 bytes; a multibyte char is >1 unit."""
    # "é" is 2 UTF-8 bytes → 0 tokens; "日本" is 6 bytes → 1 token.
    assert estimate_tokens("é") == 0
    assert estimate_tokens("日本") == 1
    assert BYTES_PER_TOKEN == 4


# -- estimate_chars ---------------------------------------------------------


def test_estimate_chars_is_inverse():
    """Token budget → char budget at 4 chars per token."""
    assert estimate_chars(0) == 0
    assert estimate_chars(1) == 4
    assert estimate_chars(1000) == 4000


# -- estimate_image_tokens --------------------------------------------------


def test_estimate_image_tokens_uses_constant():
    """image_count * IMAGE_TOKEN_ESTIMATE."""
    assert estimate_image_tokens(0) == 0
    assert estimate_image_tokens(1) == IMAGE_TOKEN_ESTIMATE
    assert estimate_image_tokens(3) == 3 * IMAGE_TOKEN_ESTIMATE
    assert IMAGE_TOKEN_ESTIMATE == 765


# -- usage_percentage -------------------------------------------------------


def test_usage_percentage_clamps_and_handles_zero_total():
    """Float percentage, clamped to 100.0; total == 0 → 0.0."""
    assert usage_percentage(0, 0) == 0.0
    assert usage_percentage(50, 100) == 50.0
    assert usage_percentage(150, 100) == 100.0
    assert usage_percentage(100, 0) == 0.0


# -- usage_percentage_u8 ----------------------------------------------------


def test_usage_percentage_u8_rounds():
    """Integer percentage via round-half-up."""
    assert usage_percentage_u8(0, 100) == 0
    assert usage_percentage_u8(50, 100) == 50
    assert usage_percentage_u8(99, 100) == 99
    # 12_700 / 256_000 = 0.04960... → 5 after rounding.
    assert usage_percentage_u8(12_700, 256_000) == 5
    assert usage_percentage_u8(150, 100) == 100


def test_usage_percentage_u8_rounds_half_up():
    """Half-boundary contract — locks the round-half-up direction.

    ``85 / 200 = 0.425`` becomes ``42.5%`` which rounds half-up to ``43``.
    The truncating helper returns ``42`` for the same input. ``7 / 8 =
    0.875`` rounds to ``88`` (truncated would be ``87``). Python's built-in
    ``round`` would give ``42`` here (banker's rounding) — this test guards
    that regression.
    """
    assert usage_percentage_u8(85, 200) == 43
    assert usage_percentage_u8(7, 8) == 88


def test_usage_percentage_u8_handles_zero_total():
    """total == 0 short-circuits to 0 rather than dividing by zero."""
    assert usage_percentage_u8(99, 0) == 0


# -- usage_percentage_truncated_u8 -----------------------------------------


def test_usage_percentage_truncated_u8_clamps_and_handles_zero_total():
    """Truncated integer percentage; large values clamp via min(100)."""
    assert usage_percentage_truncated_u8(0, 0) == 0
    assert usage_percentage_truncated_u8(50, 100) == 50
    assert usage_percentage_truncated_u8(150, 100) == 100
    # Saturating-mul overflow is invisible in Python (unbounded ints), but
    # the min(100) clamp still pins the result; mirrors Rust's u64::MAX path.
    assert usage_percentage_truncated_u8(_U64_MAX, 1) == 100


def test_usage_percentage_truncated_u8_truncates_does_not_round():
    """Truncation contract — distinguishes this helper from the rounding one.

    ``85 / 200 = 0.425``, truncated → ``42`` (rounded would be ``43``).
    ``7 / 8 = 0.875``, truncated → ``87`` (rounded would be ``88``). Locks
    in that ``exceeds_threshold(used, cw, p)`` and
    ``usage_percentage_truncated_u8(used, cw) >= p`` agree.
    """
    assert usage_percentage_truncated_u8(85, 200) == 42
    assert usage_percentage_truncated_u8(7, 8) == 87


def test_usage_percentage_truncated_u8_handles_zero_total():
    """total == 0 short-circuits to 0 rather than dividing by zero."""
    assert usage_percentage_truncated_u8(99, 0) == 0


# -- free_tokens ------------------------------------------------------------


def test_free_tokens_saturates():
    """total - used clamped at zero — never negative."""
    assert free_tokens(100, 30) == 70
    assert free_tokens(100, 100) == 0
    assert free_tokens(100, 200) == 0


# -- exceeds_threshold ------------------------------------------------------


def test_exceeds_threshold_matches_integer_pct():
    """Boolean gate over the integer percentage; cw == 0 → False."""
    assert exceeds_threshold(50, 100, 85) is False
    assert exceeds_threshold(85, 100, 85) is True
    assert exceeds_threshold(99, 100, 85) is True
    assert exceeds_threshold(50, 0, 85) is False


def test_exceeds_threshold_fires_on_strict_boundary():
    """Strict-boundary contract — pin the ``>=`` semantics.

    At ``cw=1000, pct=85``, ``850 * 100 == 1000 * 85`` so the gate fires at
    exactly 850 tokens — one token earlier than the legacy ``>`` gate.
    """
    assert exceeds_threshold(850, 1000, 85) is True
    assert exceeds_threshold(849, 1000, 85) is False
    # Same shape at the other commonly-configured threshold (95%).
    assert exceeds_threshold(950, 1000, 95) is True
    assert exceeds_threshold(949, 1000, 95) is False


# -- exceeds_threshold_with_headroom ---------------------------------------


@pytest.mark.parametrize(
    ("cw", "pct"),
    [
        (0, 0),
        (0, 1),
        (1, 50),
        (50, 85),
        (100, 99),
        (101, 100),
        (1024, 0),
        (100_000, 1),
        (128_001, 50),
        (1_000_001, 85),
    ],
)
def test_exceeds_threshold_with_headroom_zero_headroom_matches_exceeds_threshold(
    cw: int, pct: int
):
    """With headroom == 0 the helper agrees with exceeds_threshold everywhere.

    Covers a representative grid including the non-round windows where
    floor-divide would otherwise drift. Parametrized to mirror grok's nested
    loop over cw × pct × used.
    """
    for used in (0, 1, cw // 2, max(0, cw - 1), cw, cw + 1, cw + 1000):
        assert (
            exceeds_threshold_with_headroom(used, cw, pct, 0)
            == exceeds_threshold(used, cw, pct)
        ), f"mismatch at used={used} cw={cw} pct={pct}"


def test_exceeds_threshold_with_headroom_subtracts_headroom():
    """100K window, 85% threshold = 85_000; headroom 4_000 fires at 81_000."""
    assert exceeds_threshold_with_headroom(80_999, 100_000, 85, 4_000) is False
    assert exceeds_threshold_with_headroom(81_000, 100_000, 85, 4_000) is True


def test_exceeds_threshold_with_headroom_zero_window():
    """cw == 0 short-circuits to False regardless of used / headroom."""
    assert exceeds_threshold_with_headroom(0, 0, 85, 0) is False
    assert exceeds_threshold_with_headroom(100, 0, 85, 4_000) is False


def test_exceeds_threshold_with_headroom_headroom_larger_than_threshold_saturates():
    """Saturating-sub contract — headroom beyond the threshold forces a fire.

    100K * 85% = 8_500_000 scaled. Headroom 1M tokens scales to 100_000_000;
    saturating-sub clamps the right-hand side to 0, so any ``used`` fires —
    even ``used == 0``.
    """
    assert exceeds_threshold_with_headroom(0, 100_000, 85, 1_000_000) is True
