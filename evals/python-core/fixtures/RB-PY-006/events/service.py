from .serialization import events_to_json

class EventService:
    def __init__(self, events=()):
        self._events = list(events)

    def list_events(self, query=None):
        return [event for event in self._events
                if query is None or query.casefold() in event.title.casefold()]

    def export_json(self, query=None):
        return events_to_json(self.list_events(query))
