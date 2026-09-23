from collections.abc import Mapping

from .models import identifier


class StockStore:
    def __init__(self, initial_stock):
        if not isinstance(initial_stock, Mapping):
            raise ValueError("Stock must be a mapping")
        self._stock = {}
        for sku, quantity in initial_stock.items():
            identifier(sku)
            if type(quantity) is not int or quantity < 0:
                raise ValueError("Stock quantities must be nonnegative integers")
            self._stock[sku] = quantity

    def get(self, sku):
        return self._stock[identifier(sku)]

    def snapshot(self):
        return dict(self._stock)

    def deduct(self, items):
        """Check every line before deducting any stock."""
        raise NotImplementedError("Implement atomic stock deduction")

    def restore(self, items):
        """Restore quantities from a previously active reservation."""
        raise NotImplementedError("Implement stock restoration")
