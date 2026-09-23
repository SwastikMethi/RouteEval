from .store import StockStore


class InventoryService:
    def __init__(self, initial_stock):
        self._store = StockStore(initial_stock)
        self._reservations = {}

    def stock(self, sku):
        return self._store.get(sku)

    def stock_snapshot(self):
        return self._store.snapshot()

    def reserve(self, reservation_id, lines):
        raise NotImplementedError("Implement atomic reservations and safe retries")

    def get_reservation(self, reservation_id):
        raise NotImplementedError("Implement reservation lookup")

    def cancel(self, reservation_id):
        raise NotImplementedError("Implement idempotent cancellation")
