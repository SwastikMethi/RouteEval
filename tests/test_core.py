import pytest
from routebench.metrics import aggregate
from routebench.security import StreamRedactor, safe_child


def test_redaction_across_chunks_and_containment(tmp_path):
    redactor = StreamRedactor(["secret-value-123"])
    out = redactor.feed("hello secret-va") + redactor.feed("lue-123\n") + redactor.flush()
    assert "secret-value-123" not in out and "REDACTED" in out
    with pytest.raises(ValueError):
        safe_child(tmp_path, "../escape")
    (tmp_path / "link").symlink_to(tmp_path.parent)
    with pytest.raises(ValueError):
        safe_child(tmp_path, "link/out")


def test_macro_average_and_missing_cost():
    run = {
        "profiles": [{"id": "a", "label": "A"}],
        "suite": {"cases": [{"id": "one", "category": "bug"}, {"id": "two", "category": "feature"}]},
    }
    attempts = [
        {"id": "1", "profile_id": "a", "case_id": "one", "status": "passed", "score": 1.0, "full_pass": True},
        {
            "id": "2",
            "profile_id": "a",
            "case_id": "one",
            "status": "failed",
            "score": 0.0,
            "full_pass": False,
        },
        {"id": "3", "profile_id": "a", "case_id": "two", "status": "passed", "score": 1.0, "full_pass": True},
    ]
    result = aggregate(run, attempts)
    row = result["leaderboard"][0]
    assert row["mean_score"] == 0.75
    assert row["total_cost_usd"] is None
    assert result["winners"]["cheapest_successful"] is None
    assert row["pass_rate"] == pytest.approx(2 / 3)
