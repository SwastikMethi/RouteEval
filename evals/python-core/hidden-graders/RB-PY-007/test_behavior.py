import pytest
from transports import UnknownTransport, create_transport, preview, register, send
from transports.client import create_transport as compatibility_create
from transports.sender import send as compatibility_send
from transports.preview import preview as compatibility_preview
from transports.registration import register as compatibility_register
from transports.implementations import MemoryTransport, ConsoleTransport, FileTransport

def test_builtins_arguments_and_compatibility():
    for name, cls in [("memory", MemoryTransport), ("console", ConsoleTransport), ("file", FileTransport)]:
        endpoint = object()
        payload = {"content": [1, 2]}
        instance = compatibility_create(name, endpoint, timeout=0.25)
        assert type(instance) is cls and instance.endpoint is endpoint and instance.timeout == .25
        result = compatibility_send(name, endpoint, payload, timeout=.5)
        assert result == {"transport": name, "endpoint": endpoint, "timeout": .5, "payload": payload}
        assert result["payload"] is payload
        assert compatibility_preview(name, endpoint, timeout=8)["timeout"] == 8

def test_shared_custom_registration_and_collisions():
    calls = []
    class Custom:
        def __init__(self, endpoint, *, timeout):
            calls.append((endpoint, timeout))
        def send(self, payload):
            return ("sent", payload)
        def describe(self):
            return "custom-description"
    compatibility_register("custom-hidden", Custom)
    assert isinstance(create_transport("custom-hidden", "e", timeout=0), Custom)
    assert send("custom-hidden", "f", 7, timeout=12) == ("sent", 7)
    assert preview("custom-hidden", "g", timeout=4) == "custom-description"
    assert calls == [("e", 0), ("f", 12), ("g", 4)]
    for name in ["memory", "console", "file", "custom-hidden"]:
        with pytest.raises(ValueError):
            register(name, lambda *args, **kwargs: None)
    assert preview("custom-hidden", "after") == "custom-description"
    assert create_transport("memory", "after").kind == "memory"

def test_unknown_and_constructor_errors_propagate():
    for operation in [lambda: create_transport("absent", "e"), lambda: send("absent", "e", "p"), lambda: preview("absent", "e")]:
        with pytest.raises(UnknownTransport) as error:
            operation()
        assert error.value.args == ("absent",)
    failure = RuntimeError("factory failed")
    def broken(endpoint, *, timeout):
        raise failure
    register("broken-hidden", broken)
    for operation in [lambda: create_transport("broken-hidden", "e"), lambda: send("broken-hidden", "e", "p"), lambda: preview("broken-hidden", "e")]:
        with pytest.raises(RuntimeError) as error:
            operation()
        assert error.value is failure
