from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http_retry import RetryPolicy, parse_retry_after

def test_http_dates_and_clock_offsets():
    instant = datetime(2025, 1, 1, tzinfo=timezone.utc)
    for seconds in [-300, 0, 1, 71, 86401]:
        header = format_datetime(instant + timedelta(seconds=seconds), usegmt=True)
        assert parse_retry_after(header, now=lambda: instant) == max(seconds, 0)
    east = timezone(timedelta(hours=5, minutes=30))
    header = format_datetime((instant + timedelta(seconds=23)).astimezone(east))
    assert parse_retry_after(header, now=lambda: instant) == 23
    assert parse_retry_after("Wed, 01 Jan 2025 00:00:09", now=lambda: instant.replace(tzinfo=None)) == 9

def test_policy_uses_parser_and_injected_clock():
    clock = lambda: datetime(2025, 1, 1, tzinfo=timezone.utc)
    assert RetryPolicy(clock, maximum=15).delay({"rEtRy-AfTeR": "Wed, 01 Jan 2025 00:00:20 GMT"}) == 15
    assert RetryPolicy(clock, fallback=3).delay({}) == 3
