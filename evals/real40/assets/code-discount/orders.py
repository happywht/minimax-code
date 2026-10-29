def create_order(price_cents, quantity):
    subtotal = price_cents * quantity
    return {"subtotal_cents": subtotal, "discount_cents": 0, "total_cents": subtotal, "rate_percent": 0}
