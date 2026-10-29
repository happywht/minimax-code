def update_all(stock, changes):
    for key, delta in changes.items(): stock[key] += delta
    return stock
