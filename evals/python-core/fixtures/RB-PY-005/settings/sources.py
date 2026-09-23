ENV_KEYS = {"APP_DEBUG": "debug", "APP_RETRIES": "retries", "APP_TIMEOUT": "timeout",
            "APP_DATABASE__HOST": "database.host", "APP_DATABASE__PORT": "database.port"}

def read_environment(env):
    result = {}
    for variable, key in ENV_KEYS.items():
        if variable not in env or env[variable] is None:
            continue
        value = env[variable]
        if key == "debug":
            value = bool(value)
        elif key in ("retries", "timeout", "database.port"):
            value = int(value)
        if "." in key:
            parent, child = key.split(".")
            result.setdefault(parent, {})[child] = value
        else:
            result[key] = value
    return result
