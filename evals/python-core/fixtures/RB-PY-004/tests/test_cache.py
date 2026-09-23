from cache import TTLCache

def test_existing_cache_api():
    cache = TTLCache()
    cache.set("a", 2)
    cache.set("none", None)
    assert cache.get("a") == 2 and "a" in cache
    assert cache.get("missing", 9) == 9
    assert cache.get("none", 9) is None
    assert len(cache) == 2
    cache.clear()
    assert len(cache) == 0
