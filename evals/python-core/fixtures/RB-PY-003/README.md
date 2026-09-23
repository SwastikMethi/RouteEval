# Inventory catalog
`Item(sku, category, price, available)` is a frozen record. Category, price,
and availability may be None. `Catalog(items)` snapshots the supplied iterable.
Implement `Catalog.filter(*, category=None, available=None, max_price=None)`.
Each None argument disables that filter. Category uses exact, case-sensitive
matching. Availability distinguishes True and False; missing availability
matches neither. Maximum price is inclusive; when enabled it excludes missing
prices, and zero is an enabled price limit. Enabled filters combine with AND.
Return a new list in original input order, preserving original Item objects,
and never mutate the catalog or caller's list. No filters returns all items.
`get(sku)` and `total_known_value()` must remain compatible.
Change only `inventory/`; run `python -m pytest -q tests`.
