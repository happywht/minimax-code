def cancel(order):
    order["status"] = "cancelled"
    return order
