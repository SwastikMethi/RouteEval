import csv
from io import StringIO
from events import Event, EventService
from events import serialization

def test_empty_and_deterministic_csv():
    assert serialization.events_to_csv([]) == "id,title,notes,attendees\r\n"
    events = [Event(9, "Second", "note", 0), Event(1, "First", "", 4)]
    result = serialization.events_to_csv(iter(events))
    assert result == "id,title,notes,attendees\r\n9,Second,note,0\r\n1,First,,4\r\n"
    assert EventService(events).export_csv() == result

def test_csv_escaping_and_unicode_round_trip():
    events = [Event(7, 'Hello, "世界"', "line1\nline2\rthird", 12), Event(8, "Café", "a,b", 0)]
    before = events[:]
    result = serialization.events_to_csv(events)
    rows = list(csv.reader(StringIO(result, newline="")))
    assert rows == [["id", "title", "notes", "attendees"], ["7", events[0].title, events[0].notes, "12"], ["8", "Café", "a,b", "0"]]
    assert events == before
