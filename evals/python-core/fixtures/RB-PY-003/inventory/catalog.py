from decimal import Decimal

class Catalog:
    def __init__(self, items):
        self._items = list(items)

    def get(self, sku):
        return next((item for item in self._items if item.sku == sku), None)

    def total_known_value(self):
        return sum((item.price for item in self._items if item.price is not None), Decimal(0))

    def filter(self, *, category=None, available=None, max_price=None):
        """Return original records matching every enabled filter, in input order."""
        return list(self._items)
