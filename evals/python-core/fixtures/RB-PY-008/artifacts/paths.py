from pathlib import Path

def normalize_path(root, relative):
    base = Path(root).resolve()
    target = (base / relative).resolve()
    if not str(target).startswith(str(base)):
        raise ValueError("artifact path escapes root")
    return target
