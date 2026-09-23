from decimal import Decimal
from inventory import Catalog, Item

def test_existing_catalog_operations():
    item = Item("one", "tools", Decimal("2.5"), True)
    catalog = Catalog([item])
    assert catalog.get("one") is item
    assert catalog.get("missing") is None
    assert catalog.total_known_value() == Decimal("2.5")
    assert catalog.filter() == [item]
