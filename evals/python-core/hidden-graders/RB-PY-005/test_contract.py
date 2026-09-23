"""Declared runtime constraints; hidden tests themselves may use pytest."""
import ast
from pathlib import Path
import sys


def test_runtime_uses_standard_library_and_local_modules():
    root = Path.cwd()
    local = {path.stem for path in root.iterdir() if path.is_file() and path.suffix == ".py"}
    local.update(path.name for path in root.iterdir() if path.is_dir() and (path / "__init__.py").is_file())
    allowed = sys.stdlib_module_names | local
    for path in root.rglob("*.py"):
        parts = path.relative_to(root).parts
        if any(part.startswith(".") or part in {"tests", "__hidden__", "__pycache__"} for part in parts):
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] in allowed for alias in node.names), "runtime dependency is not standard-library or local"
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                assert node.module.split(".")[0] in allowed, "runtime dependency is not standard-library or local"
