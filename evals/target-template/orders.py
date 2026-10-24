"""Order pipeline — depends on ``inventory`` and (task 2) ``discount``."""

from __future__ import annotations

import inventory


def validate_order(item_id: int, quantity: int) -> None:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    item = inventory.lookup(item_id)
    if int(item["quantity"]) < quantity:  # type: ignore[call-arg]
        raise ValueError("insufficient stock")


def create_order(item_id: int, quantity: int) -> dict[str, object]:
    """Create an order dict after validating stock. TASK-2: apply discount here."""
    validate_order(item_id, quantity)
    item = inventory.lookup(item_id)
    unit_price = float(item["price"])  # type: ignore[call-arg]
    total = unit_price * quantity
    return {
        "item_id": item_id,
        "quantity": quantity,
        "unit_price": unit_price,
        "total": round(total, 2),
    }
