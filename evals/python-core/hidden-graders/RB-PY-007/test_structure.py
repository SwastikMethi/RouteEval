import ast
from pathlib import Path

def test_duplicate_transport_selection_is_removed():
    builtins = {"memory", "console", "file"}
    for path in Path("transports").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                # Membership in a registry is fine; literal name selection is not.
                literals = [value.value for value in [node.left, *node.comparators]
                            if isinstance(value, ast.Constant) and isinstance(value.value, str)]
                assert not builtins.intersection(literals), "built-in transport selection still branches by name"
            if isinstance(node, ast.MatchValue) and isinstance(node.value, ast.Constant):
                assert node.value.value not in builtins, "built-in transport match branches remain"
