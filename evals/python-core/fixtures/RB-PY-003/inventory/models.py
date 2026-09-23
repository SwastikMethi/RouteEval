from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True)
class Item:
    sku: str
    category: str | None
    price: Decimal | None
    available: bool | None
