def parse_retry_after(value, *, now, fallback=1.0):
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return fallback
