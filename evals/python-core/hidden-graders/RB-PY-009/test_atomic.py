import pytest
from inventory import InventoryService, Reservation


def test_reservation_normalizes_generators_and_deducts_all_items():
    service = InventoryService({"apple": 10, "pear": 8, "unused": 0})
    result = service.reserve("order", iter([("pear", 2), ("apple", 1), ("apple", 3)]))
    assert result == Reservation("order", (("apple", 4), ("pear", 2)), "active")
    assert service.get_reservation("order") == result
    assert service.stock_snapshot() == {"apple": 6, "pear": 6, "unused": 0}


@pytest.mark.parametrize("lines,error", [
    ([("apple", 2), ("pear", 3)], ValueError),
    ([("apple", 4), ("apple", 2)], ValueError),
    ([("apple", 1), ("missing", 1)], KeyError),
    ([("apple", 99), ("missing", 1)], KeyError),
    ([("apple", 1), ("pear", True)], ValueError),
    ([("missing", 1), ("pear", 0)], ValueError),
])
def test_rejection_is_atomic_and_does_not_consume_id(lines, error):
    service = InventoryService({"apple": 5, "pear": 2})
    before = service.stock_snapshot()
    with pytest.raises(error):
        service.reserve("retryable", iter(lines))
    assert service.stock_snapshot() == before
    with pytest.raises(KeyError):
        service.get_reservation("retryable")
    assert service.reserve("retryable", [("apple", 5)]).status == "active"
    assert service.stock("apple") == 0


@pytest.mark.parametrize("lines", [
    None, 3, True, [], iter([]), "apple", [("apple",)], [("apple", 1, 2)],
    [("apple", 0)], [("apple", -1)], [("apple", True)], [("apple", 1.0)],
    [("apple", "1")], [("", 1)], [(" \t", 1)], [(None, 1)], [(7, 1)],
])
def test_invalid_lines(lines):
    service = InventoryService({"apple": 5})
    with pytest.raises(ValueError):
        service.reserve("order", lines)
    assert service.stock_snapshot() == {"apple": 5}


@pytest.mark.parametrize("identifier", [None, 0, True, [], "", " \n"])
def test_invalid_identifiers(identifier):
    service = InventoryService({"apple": 5})
    for operation in (
        lambda: service.reserve(identifier, [("apple", 1)]),
        lambda: service.get_reservation(identifier),
        lambda: service.cancel(identifier),
        lambda: service.stock(identifier),
    ):
        with pytest.raises(ValueError):
            operation()
    assert service.stock("apple") == 5


@pytest.mark.parametrize("initial", [None, [], {1: 2}, {" ": 2}, {"x": -2}, {"x": False}, {"x": 2.0}])
def test_initial_stock_validation(initial):
    with pytest.raises(ValueError):
        InventoryService(initial)


def test_exact_skus_empty_stock_and_large_integer_quantities():
    service = InventoryService({"Apple": 10**20, "apple": 2, " apple ": 1})
    record = service.reserve(" Order ", [("Apple", 10**20), (" apple ", 1)])
    assert record.reservation_id == " Order "
    assert service.stock_snapshot() == {"Apple": 0, "apple": 2, " apple ": 0}
    with pytest.raises(KeyError):
        service.get_reservation("Order")
    empty = InventoryService({})
    with pytest.raises(KeyError):
        empty.reserve("one", [("apple", 1)])
