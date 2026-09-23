import pytest
from artifacts import normalize_path

def test_absolute_traversal_windows_and_prefix_collisions(tmp_path):
    root = tmp_path / "artifacts"
    root.mkdir()
    for value in ["../outside.txt", "a/../../outside.txt", "../artifacts-other/file", "/tmp/file", str(root / "inside"), "C:/file", "c:file", r"\\server\share", r"a\b"]:
        with pytest.raises(ValueError):
            normalize_path(root, value)

def test_existing_symlink_resolution(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / "real").mkdir()
    (root / "good").symlink_to(root / "real", target_is_directory=True)
    (root / "bad").symlink_to(tmp_path, target_is_directory=True)
    assert normalize_path(root, "good/file") == root / "real/file"
    with pytest.raises(ValueError):
        normalize_path(root, "bad/escape")
