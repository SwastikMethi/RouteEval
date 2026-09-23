import time

class TTLCache:
    def __init__(self, *, clock=time.monotonic):
        self._clock = clock
        self._values = {}

    def set(self, key, value, ttl=None):
        self._values[key] = value

    def get(self, key, default=None):
        return self._values.get(key, default)

    def __contains__(self, key):
        return key in self._values

    def __len__(self):
        return len(self._values)

    def clear(self):
        self._values.clear()
