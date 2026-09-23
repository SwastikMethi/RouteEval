from datetime import datetime, timezone
from http_retry import RetryPolicy, parse_retry_after

NOW = lambda: datetime(2025, 1, 1, tzinfo=timezone.utc)

def test_integer_and_policy_compatibility():
    assert parse_retry_after("12", now=NOW) == 12
    assert parse_retry_after(None, now=NOW, fallback=7) == 7
    assert RetryPolicy(NOW, maximum=5).delay({"Retry-After": "12"}) == 5
