from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class Invoice:
    number: str
    amount: int | float | Decimal
