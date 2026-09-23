from decimal import Decimal
from random import Random
from inventory import Catalog, Item

def test_missing_zero_empty_and_input_identity():
    rows = [Item("a", None, None, None), Item("b", "", Decimal(0), False), Item("c", "", Decimal(-1), True)]
    before = rows[:]
    catalog = Catalog(iter(rows))
    assert catalog.filter(max_price=0) == rows[1:]
    assert catalog.filter(available=False) == [rows[1]]
    assert catalog.filter(category="") == rows[1:]
    assert catalog.filter() == rows
    assert catalog.filter()[0] is rows[0]
    result = catalog.filter()
    result.clear()
    assert catalog.filter() == before and rows == before
    assert Catalog([]).filter(available=True) == []

def test_composition_preserves_order():
    rng = Random(73)
    rows = [Item(str(i), rng.choice([None, "a", "b"]), rng.choice([None, Decimal(0), Decimal(7)]), rng.choice([None, False, True])) for i in range(40)]
    catalog = Catalog(rows)
    for category in [None, "a", "b"]:
        for available in [None, False, True]:
            for limit in [None, Decimal(0), Decimal(7)]:
                expected = [row for row in rows if (category is None or row.category == category) and (available is None or row.available is available) and (limit is None or row.price is not None and row.price <= limit)]
                assert catalog.filter(category=category, available=available, max_price=limit) == expected
