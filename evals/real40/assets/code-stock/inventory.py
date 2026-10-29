"""Inventory valuation; prices are integer cents."""
CATALOG = {"A-1": (1299, 3), "B-2": (250, 12), "C-3": (4999, 0)}

def total_cents(catalog=CATALOG):
    return sum(price for price, quantity in catalog.values())
