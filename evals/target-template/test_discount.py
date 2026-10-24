"""Discount tests. TASK-3 fixes the wrong EXPECTATION here (code is right).
TASK-6 fixes discount.py's wrong boundary (the code is wrong there)."""

from __future__ import annotations

import discount


def test_no_discount_below_tier():
    assert discount.discount_rate(3) == 0.0


def test_ten_percent_tier():
    assert discount.discount_rate(15) == 0.10


def test_twenty_percent_tier():
    assert discount.discount_rate(80) == 0.20


def test_apply_rounds_to_cents():
    assert discount.apply(199.0, 15) == 179.1


# TASK-6: uncomment these once discount_rate's 10% boundary is fixed.
# def test_exactly_ten_units_gets_ten_percent():
#     assert discount.discount_rate(10) == 0.10
#
#
# def test_nine_units_get_no_discount():
#     assert discount.discount_rate(9) == 0.0
