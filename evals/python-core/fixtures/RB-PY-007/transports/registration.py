_factories = {}

def register(name, factory):
    if name in ("memory", "console", "file") or name in _factories:
        raise ValueError(f"transport already registered: {name}")
    _factories[name] = factory
