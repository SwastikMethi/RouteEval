import random
from dataclasses import FrozenInstanceError, is_dataclass

import pytest
from inventory import InventoryService, Reservation


def test_equivalent_retry_before_and_after_cancellation():
    service = InventoryService({"a": 3, "b": 2})
    active = service.reserve("one", [("b", 2), ("a", 3)])
    assert service.reserve("one", [("a", 1), ("b", 2), ("a", 2)]) == active
    assert service.stock_snapshot() == {"a": 0, "b": 0}
    cancelled = service.cancel("one")
    assert cancelled == Reservation("one", (("a", 3), ("b", 2)), "cancelled")
    assert service.cancel("one") == cancelled
    assert service.reserve("one", iter([("a", 3), ("b", 2)])) == cancelled
    assert service.stock_snapshot() == {"a": 3, "b": 2}
    assert service.get_reservation("one") == cancelled
    assert active.status == "active"


def test_conflicts_and_invalid_retries_never_change_history():
    service = InventoryService({"a": 9})
    for state in ("active", "cancelled"):
        if state == "active":
            expected = service.reserve("one", [("a", 2)])
        else:
            expected = service.cancel("one")
        before = service.stock_snapshot()
        for lines in ([("a", 1)], [("missing", 1)], [], [("a", True)]):
            with pytest.raises(ValueError):
                service.reserve("one", lines)
            assert service.stock_snapshot() == before
            assert service.get_reservation("one") == expected


def test_isolation_and_immutable_records():
    original = {"a": 10}
    service = InventoryService(original)
    other = InventoryService(original)
    original["a"] = 999
    lines = [["a", 3]]
    record = service.reserve("one", lines)
    lines[0][1] = 999
    assert is_dataclass(record) and isinstance(record, Reservation)
    assert record.items == (("a", 3),)
    with pytest.raises(FrozenInstanceError):
        record.status = "cancelled"
    with pytest.raises(TypeError):
        record.items[0][1] = 9
    snapshot = service.stock_snapshot()
    snapshot.clear()
    assert service.stock("a") == 7
    assert other.stock("a") == 10
    with pytest.raises(KeyError):
        other.get_reservation("one")
    for operation in (service.cancel, service.get_reservation):
        with pytest.raises(KeyError):
            operation("missing")
    assert service.stock("a") == 7


def test_interleaved_reservations_restore_only_their_own_stock():
    rng = random.Random(9042)
    initial = {"a": 500, "b": 500, "c": 500}
    expected = dict(initial)
    service = InventoryService(initial)
    records = []
    for index in range(20):
        lines = [(sku, rng.randint(1, 5)) for sku in sorted(initial)]
        record = service.reserve(str(index), lines)
        records.append(record)
        for sku, quantity in lines:
            expected[sku] -= quantity
        assert service.stock_snapshot() == expected
        assert service.reserve(str(index), reversed(lines)) == record
    rng.shuffle(records)
    for record in records:
        service.cancel(record.reservation_id)
        for sku, quantity in record.items:
            expected[sku] += quantity
        service.cancel(record.reservation_id)
        assert service.stock_snapshot() == expected
    assert expected == initial
