# Local cache
Extend `TTLCache(*, clock=time.monotonic)` and `set(key, value, ttl=None)` with
per-entry expiration. The injected clock is a zero-argument callable returning
seconds. TTL None never expires; numeric TTL >= 0 expires at clock()+ttl.
An entry is expired when now >= deadline, including immediate expiry for zero.
Negative TTL raises ValueError without modifying the existing entry. `get(key,
default=None)`, `key in cache`, and `len(cache)` must all lazily discard expired
entries. Length counts only live entries. Replacing a key resets its deadline;
replacing with no TTL removes its old deadline. Values may be None. Keys may be
any hashable value. Keep the no-TTL API and clear(). No sleeps or background work.
Change only `cache.py`; run `python -m pytest -q tests`.
