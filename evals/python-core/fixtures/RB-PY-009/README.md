# Inventory reservations: plan, implement, verify

Add reservations to this in-memory inventory service. Work in one session through
planning, implementation, and verification. Python 3.11+ and the standard library
are sufficient; pytest is supplied by the evaluation environment.

## Workflow

1. Inspect `inventory/` and the existing tests. Before editing implementation
   files, create `PLAN.md` with these level-two headings and substantive content:
   `## Requirements`, `## Approach`, `## Failure cases`, and `## Testing`.
   Describe the requirements below, affected components, failure cases, and how
   you will test them. A concise engineering plan is sufficient.
2. Implement the reservation behavior and add focused pytest tests in
   `tests/test_reservations.py`. Preserve all existing tests and public APIs.
3. Run `python -m pytest -q tests`, fix failures, and create `VERIFICATION.md`
   with `## Commands`, `## Outcomes`, and `## Limitations`. Report what you
   actually ran, including unresolved failures or limitations. If none remain,
   say so. Finish with a short summary of changes and verification.

You may change only `inventory/**`, `tests/test_reservations.py`, `PLAN.md`, and
`VERIFICATION.md`. Do not change this README, existing tests, or dependencies.
No network access, persistence, database, concurrency, or wall-clock behavior is
required. Hidden tests check only the behavior and constraints documented here.

## Public contract

Keep these imports working: `from inventory import InventoryService, Reservation`.

### R1: Existing stock behavior

`InventoryService(initial_stock)` takes a mapping of SKU strings to quantities.
An empty mapping is allowed. SKUs must be nonblank strings; matching is exact and
case-sensitive, and whitespace is not stripped from valid SKUs. Stock quantities
must be nonnegative integers; booleans and floats are invalid. Invalid initial
stock raises `ValueError`. Snapshot the mapping rather than retaining it.

`stock(sku)` returns the available quantity. `stock_snapshot()` returns a new
dictionary of all available quantities. Invalid SKU arguments raise `ValueError`;
well-formed unknown SKUs raise `KeyError`. Preserve existing stock-query behavior.

### R2: Validate and reserve atomically

`reserve(reservation_id, lines)` accepts a nonblank string ID and a nonempty
iterable of `(sku, quantity)` pairs, including generators. Quantities must be
positive integers, excluding booleans. Malformed IDs, lines, SKUs, or quantities
raise `ValueError`. Combine duplicate SKUs by adding their quantities; normalize
the result to a tuple of `(sku, quantity)` pairs sorted by SKU.

For a new ID, validate the complete input before checking stock. A well-formed
unknown SKU raises `KeyError`; insufficient stock raises `ValueError`. Check all
SKUs exist before checking availability. Only after all checks succeed, decrease
stock for every line and save an active reservation. Any failure leaves both
stock and reservation history unchanged; the failed ID remains available.

### R3: Safe retries

For an existing ID, validate and normalize the incoming lines first. If they
equal the stored items, return the existing reservation without another stock
change, even when current stock is insufficient for a new reservation. Input
order and splitting one quantity across duplicate lines do not affect equality.
If the items differ, raise `ValueError` without changing any state.

### R4: Cancellation

`cancel(reservation_id)` restores an active reservation's quantities exactly once
and replaces its record with a cancelled record. It returns that record. Calling
it again returns the cancelled record without restoring more stock. The ID stays
in history: an equivalent `reserve` retry returns the cancelled record without
reactivating it; different items still raise `ValueError`.

### R5: Read isolation and records

`get_reservation(reservation_id)` returns the current record. Both lookup and
cancellation raise `ValueError` for malformed IDs and `KeyError` for unknown IDs.

`Reservation` is a frozen dataclass with fields `reservation_id: str`,
`items: tuple[tuple[str, int], ...]`, and `status: str` (`active` or `cancelled`).
Previously returned active records remain active after cancellation; do not
mutate them. Changing caller-owned inputs or returned stock dictionaries must
not change internal stock or history. Service instances are independent.

### R6: Compatibility and verification

Keep runtime imports standard-library/local only. Preserve existing tests and
add executable pytest tests in the permitted new test file. Produce both
workflow documents with the required nonempty sections.

## Evaluation

Input validation and atomicity contribute 40%, retry/cancellation/isolation 40%,
visible tests 10%, permitted changes 5%, and runtime/workflow constraints 5%.
Every group is mandatory for a full pass. Workflow checks establish artifact
completeness, not plan quality, execution order, or the truth of reported test
outcomes. Independent tests establish implementation correctness. Model changes
are observed through the normal route timeline, not scored or required.
