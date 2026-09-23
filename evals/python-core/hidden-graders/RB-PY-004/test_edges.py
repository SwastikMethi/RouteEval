import pytest
from cache import TTLCache

def test_overwrites_non_expiring_and_none_values():
    now = [0]
    cache = TTLCache(clock=lambda: now[0])
    cache.set("a", 1, ttl=2)
    now[0] = 1
    cache.set("a", 2, ttl=4)
    now[0] = 2
    assert cache.get("a") == 2
    cache.set("a", None)
    cache.set(("tuple", 1), 4)
    now[0] = 1000
    assert "a" in cache and cache.get("a", 9) is None and len(cache) == 2
    cache.set("a", 5, ttl=1)
    now[0] = 1001
    assert "a" not in cache and len(cache) == 1

def test_negative_ttl_is_atomic_and_clear_removes_deadlines():
    now = [0]
    cache = TTLCache(clock=lambda: now[0])
    cache.set("a", "original", ttl=3)
    with pytest.raises(ValueError):
        cache.set("a", "bad", ttl=-1)
    assert cache.get("a") == "original"
    cache.clear()
    cache.set("a", "new")
    now[0] = 10
    assert cache.get("a") == "new" and len(cache) == 1
