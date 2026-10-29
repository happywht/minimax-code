class Stock:
    def __init__(self, quantity): self.quantity=quantity
    def take(self):
        self.quantity -= 1
        return True
