class MemoryTransport:
    kind = "memory"

    def __init__(self, endpoint, *, timeout=5):
        self.endpoint = endpoint
        self.timeout = timeout

    def describe(self):
        return {"transport": self.kind, "endpoint": self.endpoint, "timeout": self.timeout}

    def send(self, payload):
        return {**self.describe(), "payload": payload}

class ConsoleTransport(MemoryTransport):
    kind = "console"

class FileTransport(MemoryTransport):
    kind = "file"
