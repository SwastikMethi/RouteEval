from decimal import Decimal
from inventory import Catalog, Item

def test_each_filter_and_combination():
    rows = [Item("a", "book", Decimal("3"), True), Item("b", "tool", Decimal("2"), False),
            Item("c", "book", Decimal("2"), False), Item("d", "book", Decimal("2"), True)]
    catalog = Catalog(rows)
    assert catalog.filter(category="book") == [rows[0], rows[2], rows[3]]
    assert catalog.filter(available=False) == [rows[1], rows[2]]
    assert catalog.filter(max_price=Decimal("2")) == rows[1:]
    assert catalog.filter(category="book", available=True, max_price=Decimal("2")) == [rows[3]]
    assert catalog.filter(category="BOOK") == []
