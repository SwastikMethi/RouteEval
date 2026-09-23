from datetime import datetime, timezone
from http_retry import parse_retry_after

NOW = lambda: datetime(2025, 1, 1, tzinfo=timezone.utc)

def test_delta_seconds_and_invalid_fallback():
    for value, expected in [("0", 0), (" 17\t", 17), ("0009", 9), ("456", 456)]:
        assert parse_retry_after(value, now=NOW, fallback=13) == expected
    for value in [None, "", " ", "-1", "+4", "2.5", "tomorrow", "١٢", 7, True,
                  "Wed, 99 Jan 2025 00:00:00 GMT"]:
        assert parse_retry_after(value, now=NOW, fallback=13) == 13
