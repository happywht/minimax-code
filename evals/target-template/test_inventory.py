"""Inventory tests. TASK-1 makes these pass; TASK-5 adds defensive cases."""

from __future__ import annotations

import inventory


def test_lookup_returns_item():
    assert inventory.lookup(1)["name"] == "keyboard"


def test_total_value_sums_price_times_quantity():
    # TASK-1: this currently fails because total_value ignores price.
    expected = 199.0 * 12 + 89.5 * 30 + 1299.0 * 5 + 149.0 * 0
    assert inventory.total_value() == expected


def test_in_stock_reflects_quantity():
    assert inventory.in_stock(1) is True
    assert inventory.in_stock(4) is False


# TASK-5: uncomment these once inventory.lookup rejects invalid ids.
# def test_lookup_rejects_unknown_id():
#     import pytest
#     with pytest.raises(KeyError):
#         inventory.lookup(999)
#
#
# def test_lookup_rejects_negative_id():
#     import pytest
#     with pytest.raises((KeyError, ValueError)):
#         inventory.lookup(-1)
