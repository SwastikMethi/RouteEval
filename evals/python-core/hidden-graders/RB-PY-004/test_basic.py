from cache import TTLCache

def test_expiration_boundaries_and_zero():
    now = [100.0]
    cache = TTLCache(clock=lambda: now[0])
    cache.set("a", 7, ttl=2.5)
    now[0] = 102.49
    assert cache.get("a") == 7 and "a" in cache
    now[0] = 102.5
    assert cache.get("a", "gone") == "gone" and "a" not in cache
    cache.set("zero", 1, ttl=0)
    assert cache.get("zero") is None and "zero" not in cache
    cache.set("later", 3, ttl=4)
    now[0] += 50
    assert cache.get("later") is None

def test_all_read_operations_expire_independently():
    for operation in [lambda c: c.get("a"), lambda c: "a" in c, lambda c: len(c)]:
        now = [0]
        cache = TTLCache(clock=lambda: now[0])
        cache.set("a", 1, ttl=1)
        now[0] = 1
        assert not operation(cache)
        assert len(cache) == 0
