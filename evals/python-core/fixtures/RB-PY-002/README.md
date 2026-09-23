# Retry scheduling
`http_retry.parse_retry_after(value, *, now, fallback=1.0)` parses an HTTP
Retry-After header. `now` is an injected callable returning a datetime. Accept
ASCII non-negative integer seconds with surrounding whitespace, or HTTP-date
strings understood by `email.utils.parsedate_to_datetime`. Dates return seconds
from the injected clock, clamped to zero. Interpret naive header dates and naive
clock datetimes as UTC; respect explicit timezone offsets. Never read the real
clock. Return `fallback` unchanged for None, non-strings, blank values, negative
seconds, fractions, invalid dates, and malformed values. Zero is valid.

`RetryPolicy.delay(headers)` finds Retry-After case-insensitively and caps the
parsed delay at `maximum`. Keep its constructor, fallback, and cap behavior.
Change only `http_retry/`. Use standard-library parsing; no network or sleeps.
Run `python -m pytest -q tests`.
