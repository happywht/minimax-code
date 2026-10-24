"""Inventory management — the module under evaluation.

Deliberately small and readable: every evaluation task can be verified
by running pytest.
"""

from __future__ import annotations

ITEMS: dict[int, dict[str, object]] = {
    1: {"name": "keyboard", "price": 199.0, "quantity": 12},
    2: {"name": "mouse", "price": 89.5, "quantity": 30},
    3: {"name": "monitor", "price": 1299.0, "quantity": 5},
    4: {"name": "usb-hub", "price": 149.0, "quantity": 0},
}


def lookup(item_id: int) -> dict[str, object]:
    """Return the item record for ``item_id``."""
    return ITEMS[item_id]


def total_value() -> float:
    """Total monetary value of everything in stock."""
    return sum(item["quantity"] * 1.0 for item in ITEMS.values())  # BUG-1: ignores price


def in_stock(item_id: int) -> bool:
    item = lookup(item_id)
    return int(item["quantity"]) > 0  # type: ignore[call-arg]
