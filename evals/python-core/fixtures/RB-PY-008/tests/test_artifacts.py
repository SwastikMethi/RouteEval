from artifacts import ArtifactStore, normalize_path

def test_nested_artifact_path(tmp_path):
    assert normalize_path(tmp_path, "reports/a.json") == tmp_path / "reports/a.json"
    assert ArtifactStore(tmp_path).path_for("x.txt") == tmp_path / "x.txt"
