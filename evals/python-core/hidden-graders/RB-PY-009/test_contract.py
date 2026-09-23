"""Runtime and deliverable checks; prose quality and chronology are not graded."""
import ast
import re
import sys
from pathlib import Path

import pytest


def test_runtime_uses_standard_library_and_local_modules():
    allowed = sys.stdlib_module_names | {"inventory"}
    for path in Path("inventory").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert all(alias.name.split(".")[0] in allowed for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                assert node.module.split(".")[0] in allowed


@pytest.mark.parametrize("filename,headings", [
    ("PLAN.md", ("Requirements", "Approach", "Failure cases", "Testing")),
    ("VERIFICATION.md", ("Commands", "Outcomes", "Limitations")),
])
def test_required_workflow_sections(filename, headings):
    path = Path(filename)
    assert path.is_file(), f"Missing {filename}"
    sections = re.split(r"(?m)^##\s+(.+?)\s*$", path.read_text())
    bodies = dict(zip(sections[1::2], sections[2::2]))
    for heading in headings:
        assert bodies.get(heading, "").strip(), f"Missing or empty section: {heading}"


def test_new_executable_pytest_tests_exist():
    path = Path("tests/test_reservations.py")
    assert path.is_file(), "Add focused reservation tests"
    tree = ast.parse(path.read_text())
    assert any(isinstance(node, ast.FunctionDef) and node.name.startswith("test_") for node in tree.body)
