from io import StringIO
import json
from events import Event, EventService
from events.cli import main

def test_existing_commands_and_filtering():
    service = EventService([Event(2, "Meet", "hello", 4)])
    out = StringIO()
    assert main(["list", "--query", "MEET"], service=service, stdout=out) == 0
    assert json.loads(out.getvalue())[0]["id"] == 2
    out = StringIO()
    assert main(["count"], service=service, stdout=out) == 0
    assert out.getvalue() == "1\n"
    assert service.list_events("absent") == []
