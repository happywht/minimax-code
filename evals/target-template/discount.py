"""Discount rules — BUG-2 lives here (task 6)."""

from __future__ import annotations


def discount_rate(quantity: int) -> float:
    """Volume discount: 10+ units → 10%, 50+ units → 20%, else 0%.

    BUG-2: the 10% tier fires for quantities of 10 OR FEWER because the
    comparison points the wrong way.
    """
    if quantity <= 10:
        return 0.10
    if quantity >= 50:
        return 0.20
    return 0.0


def apply(total: float, quantity: int) -> float:
    """Apply the volume discount for ``quantity`` to ``total``."""
    return round(total * (1 - discount_rate(quantity)), 2)
