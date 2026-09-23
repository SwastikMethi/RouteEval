DEFAULTS = {"debug": False, "retries": 3, "timeout": 30,
            "database": {"host": "localhost", "port": 5432}}

class ConfigError(ValueError):
    pass

def coerce_value(key, value):
    if key == "debug":
        return bool(value)
    if key in ("retries", "timeout", "database.port"):
        return int(value)
    return value
