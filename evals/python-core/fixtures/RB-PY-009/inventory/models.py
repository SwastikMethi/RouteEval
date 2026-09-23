from dataclasses import dataclass


def identifier(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Identifiers must be nonblank strings")
    return value


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    items: tuple[tuple[str, int], ...]
    status: str
