from copy import deepcopy

def merge_settings(*layers):
    """Recursively merge dictionaries, skipping absent values."""
    result = {}
    for layer in layers:
        for key, value in (layer or {}).items():
            if value is None:
                continue
            if isinstance(value, dict) and isinstance(result.get(key), dict):
                result[key] = merge_settings(result[key], value)
            else:
                result[key] = deepcopy(value)
    return result
