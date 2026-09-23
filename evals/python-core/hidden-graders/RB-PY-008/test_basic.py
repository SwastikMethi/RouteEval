import pytest
from artifacts import ArtifactStore, normalize_path

def test_relative_normalization_unicode_and_no_writes(tmp_path):
    for name, expected in [("a/b.txt", "a/b.txt"), ("a//./b.txt", "a/b.txt"), ("a/../b.txt", "b.txt"), ("résumé/世界.txt", "résumé/世界.txt"), (".hidden", ".hidden"), (" a ", " a ")]:
        assert normalize_path(tmp_path, name) == tmp_path / expected
        assert ArtifactStore(tmp_path).path_for(name) == tmp_path / expected
    assert list(tmp_path.iterdir()) == []

def test_empty_root_and_invalid_input(tmp_path):
    for value in ["", ".", "./", "a/..", None, 4, b"file", "bad\x00name"]:
        with pytest.raises(ValueError):
            normalize_path(tmp_path, value)
