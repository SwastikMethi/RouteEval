from dataclasses import dataclass
from .parser import parse_retry_after

@dataclass
class RetryPolicy:
    clock: object
    fallback: float = 1.0
    maximum: float = 60.0

    def delay(self, headers):
        value = next((value for key, value in headers.items()
                      if key.lower() == "retry-after"), None)
        return min(self.maximum, parse_retry_after(
            value, now=self.clock, fallback=self.fallback))
