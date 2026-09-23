# Invoice summaries
`invoice.average_amount(values)` returns the arithmetic mean of a finite iterable
of numbers, including generators. Empty input returns integer `0`. Integer and
float inputs retain fractional averages; Decimal-only input returns a Decimal
without conversion to float or rounding. Negative and zero amounts are valid.
Inputs are never mutated. Mixed incompatible numeric types may raise TypeError.

The summary layer calls the same average function. Preserve `Invoice`,
`total_amount`, `average_amount`, and `summarize` imports and signatures.
Change only `invoice/`; do not edit tests. Run `python -m pytest -q tests`.
