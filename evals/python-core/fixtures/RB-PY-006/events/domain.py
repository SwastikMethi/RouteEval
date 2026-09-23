from dataclasses import dataclass

@dataclass(frozen=True)
class Event:
    id: int
    title: str
    notes: str
    attendees: int
