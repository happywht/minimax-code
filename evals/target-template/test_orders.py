"""Order tests. TASK-2 wires the discount module in."""

from __future__ import annotations

import orders


def test_create_order_computes_total():
    order = orders.create_order(1, 2)
    assert order["total"] == 398.0


def test_create_order_rejects_bad_quantity():
    import pytest

    with pytest.raises(ValueError):
        orders.create_order(1, 0)


def test_create_order_rejects_overstock():
    import pytest

    with pytest.raises(ValueError):
        orders.create_order(3, 99)


# TASK-2: uncomment these once create_order applies discounts.
# def test_create_order_applies_volume_discount():
#     order = orders.create_order(1, 12)  # 12 units → 10% tier
#     assert order["discount_rate"] == 0.10
#     assert order["total"] == round(199.0 * 12 * 0.9, 2)
#
#
# def test_create_order_no_discount_below_tier():
#     order = orders.create_order(1, 3)
#     assert order["discount_rate"] == 0.0
#     assert order["total"] == 597.0
