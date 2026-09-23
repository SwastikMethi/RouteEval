import pytest
from inventory import InventoryService


def test_existing_stock_queries_and_snapshot():
    original = {"apple": 5, "pear": 0, " Apple ": 2}
    service = InventoryService(original)
    original["apple"] = 100
    assert service.stock("apple") == 5
    assert service.stock("pear") == 0
    assert service.stock(" Apple ") == 2
    snapshot = service.stock_snapshot()
    snapshot["apple"] = 200
    assert service.stock_snapshot() == {"apple": 5, "pear": 0, " Apple ": 2}
    assert InventoryService({}).stock_snapshot() == {}


def test_existing_invalid_stock_and_queries():
    for invalid in (None, [], {"": 1}, {"x": -1}, {"x": True}, {"x": 1.0}):
        with pytest.raises(ValueError):
            InventoryService(invalid)
    service = InventoryService({"apple": 5})
    with pytest.raises(KeyError):
        service.stock("missing")
    with pytest.raises(ValueError):
        service.stock(" ")
