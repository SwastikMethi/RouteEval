import pytest
from transports import UnknownTransport, create_transport, preview, send

def test_existing_transports_and_errors():
    for name in ["memory", "console", "file"]:
        assert create_transport(name, "endpoint").kind == name
        assert preview(name, "endpoint")["timeout"] == 5
        assert send(name, "endpoint", "hello")["payload"] == "hello"
    with pytest.raises(UnknownTransport):
        create_transport("missing", "endpoint")
