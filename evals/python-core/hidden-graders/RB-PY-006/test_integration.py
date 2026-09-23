import ast
from pathlib import Path
from io import StringIO
import json
from events import Event, EventService
from events.cli import main

def test_cli_formats_and_filter():
    service = EventService([Event(1, "Keep", "a", 2), Event(2, "Other", "b", 3)])
    out = StringIO()
    assert main(["list", "--format", "csv", "--query", "KEEP"], service=service, stdout=out) == 0
    assert out.getvalue() == service.export_csv("KEEP") == "id,title,notes,attendees\r\n1,Keep,a,2\r\n"
    out = StringIO()
    assert main(["list", "--format", "json"], service=service, stdout=out) == 0
    assert len(json.loads(out.getvalue())) == 2

def test_cli_service_boundary_and_stdlib_csv():
    class StubService:
        def export_csv(self, query=None):
            assert query == "wanted"
            return "service-result\r\n"
    out = StringIO()
    assert main(["list", "--format", "csv", "--query", "wanted"], service=StubService(), stdout=out) == 0
    assert out.getvalue() == "service-result\r\n"
    source = ast.parse(Path("events/serialization.py").read_text())
    assert any(isinstance(node, ast.Import) and any(alias.name == "csv" for alias in node.names) or isinstance(node, ast.ImportFrom) and node.module == "csv" for node in ast.walk(source))
