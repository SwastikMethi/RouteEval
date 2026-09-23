from .errors import UnknownTransport
from .implementations import MemoryTransport, ConsoleTransport, FileTransport
from .registration import _factories

def create_transport(name, endpoint, *, timeout=5):
    if name == "memory":
        factory = MemoryTransport
    elif name == "console":
        factory = ConsoleTransport
    elif name == "file":
        factory = FileTransport
    else:
        try:
            factory = _factories[name]
        except KeyError:
            raise UnknownTransport(name) from None
    return factory(endpoint, timeout=timeout)
