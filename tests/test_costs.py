import json
import sqlite3
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from routebench.adapters import AdapterParser
from routebench.api import create_app
from routebench.backfill_costs import backfill_costs
from routebench.config import ROOT, Settings
from routebench.costs import cost_metadata, estimate_cost
from routebench.storage import Store

PRICING = {
    "source": "Pi model catalog · global.openai.gpt-5.6-terra list price",
    "effective_date": "2026-09-23",
    "input_per_million": 2,
    "cached_input_per_million": 0.2,
    "output_per_million": 12,
}
USAGE = {
    "input_tokens": 244045,
    "cached_input_tokens": 228096,
    "output_tokens": 3180,
    "cache_write_input_tokens": 0,
    "reasoning_output_tokens": 1298,
}


def parse(turns, duplicate=False):
    parser = AdapterParser("codex", "gpt-5.6-terra")
    for usage in turns:
        parser.feed(json.dumps({"type": "turn.started"}))
        event = json.dumps({"type": "turn.completed", "usage": usage})
        parser.feed(event)
        if duplicate:
            parser.feed(event)
    return parser.finalize(0)


def test_screenshot_example_and_distinct_turns():
    result = parse([USAGE], duplicate=True)
    estimate_cost(result, PRICING)
    assert result["cost_usd"] == 0.1156772
    assert result["cost_provenance"] == "estimated"
    assert result["cache_write_tokens"] == 0
    assert result["cost_estimate"]["buckets"]["input"]["tokens"] == 15949
    assert result["cost_estimate"]["pricing"] == PRICING
    assert set(result["cost_estimate"]["buckets"]) == {"input", "cached_input", "output"}
    second = {**USAGE, "input_tokens": 1000, "cached_input_tokens": 500, "output_tokens": 200}
    result = parse([USAGE, second], duplicate=True)
    estimate_cost(result, PRICING)
    assert result["cost_usd"] == 0.1191772


@pytest.mark.parametrize(
    "overrides",
    [
        {"input_tokens": None},
        {"input_tokens": -1},
        {"output_tokens": float("nan")},
        {"cached_input_tokens": None},
        {"cached_input_tokens": 999999},
        {"output_tokens": True},
        {"cache_write_tokens": 10},
        {"usage_complete": False},
    ],
)
def test_incomplete_or_invalid_usage_is_not_priced(overrides):
    result = {**parse([USAGE]), **overrides}
    estimate_cost(result, PRICING)
    assert result["cost_usd"] is None
    assert result["cost_provenance"] == "unavailable"


def test_missing_rates_zero_usage_and_preserved_cost():
    result = parse([USAGE])
    estimate_cost(result, {**PRICING, "cached_input_per_million": None})
    assert result["cost_usd"] is None
    zero = parse([{k: 0 for k in USAGE}])
    estimate_cost(zero, PRICING)
    assert zero["cost_usd"] == 0 and zero["cost_provenance"] == "estimated"
    reported = {**result, "cost_usd": 0.4, "cost_provenance": "reported"}
    estimate_cost(reported, PRICING)
    assert reported["cost_usd"] == 0.4 and "cost_estimate" not in reported
    estimate_cost(zero, {**PRICING, "input_per_million": 999})
    assert zero["cost_estimate"]["pricing"] == PRICING


def test_partial_turns_and_cache_write_usage():
    for missing in ({"input_tokens": 10}, None, {**USAGE, "cached_input_tokens": -1}):
        result = parse([missing, USAGE])
        estimate_cost(result, PRICING)
        assert result["usage_complete"] is False and result["cost_usd"] is None
    result = parse([{**USAGE, "cache_write_input_tokens": 10}])
    assert result["cache_write_tokens"] == 10
    estimate_cost(result, {**PRICING, "cache_write_per_million": 2.5})
    assert result["cost_usd"] == 0.1157022
    parser = AdapterParser("codex", "gpt-5.6-terra")
    for obj in [
        {"type": "turn.started"},
        {"type": "turn.started"},
        {"type": "turn.completed", "usage": USAGE},
    ]:
        parser.feed(json.dumps(obj))
    assert parser.finalize(0)["usage_complete"] is False


def test_aggregate_cost_provenance():
    estimated = parse([USAGE])
    estimate_cost(estimated, PRICING)
    reported = {"cost_usd": 0, "cost_provenance": "reported"}
    missing = {"cost_usd": None, "cost_provenance": "unavailable"}
    assert cost_metadata([missing])["cost_provenance"] == "unavailable"
    assert cost_metadata([estimated, missing])["cost_provenance"] == "estimated"
    assert cost_metadata([reported])["cost_provenance"] == "reported"
    assert cost_metadata([estimated, deepcopy(estimated), reported]) == {
        "cost_provenance": "mixed",
        "cost_sources": [PRICING],
    }


def saved_run(tmp_path):
    profile = {"id": "terra", "adapter": "codex", "model": "gpt-5.6-terra", "pricing": None}
    settings = SimpleNamespace(
        database=tmp_path / "bench.db",
        artifacts=tmp_path / "artifacts",
        profiles={"terra": {**profile, "pricing": PRICING}},
    )
    run = {
        "id": "run_test",
        "status": "completed",
        "profiles": [profile],
        "warnings": [],
        "security_blocked": True,
    }
    result = parse([USAGE])
    result.pop("usage_complete")
    result["cache_write_tokens"] = None  # Old parser omitted this native field.
    attempt = {
        **result,
        "id": "att_test",
        "run_id": run["id"],
        "profile_id": "terra",
        "case_id": "RB-PY-001",
        "status": "passed",
        "score": 1,
        "full_pass": True,
        "exit_code": 0,
        "artifacts": [],
    }
    store = Store(settings.database, settings.artifacts)
    store.save_run(run)
    store.save_attempt(attempt)
    path = store.artifact_dir(run["id"], attempt["id"]) / "stdout.log"
    path.write_text(
        json.dumps({"type": "turn.started"})
        + "\n"
        + json.dumps({"type": "turn.completed", "usage": USAGE})
        + "\n"
    )
    attempt["artifacts"] = [store.register_artifact(attempt, "stdout", path)]
    store.save_attempt(attempt)
    store.close()
    return settings, run, attempt, path


def test_backfill_is_audited_idempotent_and_preserves_evidence(tmp_path):
    settings, run, before, path = saved_run(tmp_path)
    raw = path.read_bytes()
    assert backfill_costs(settings)["eligible"] == 1
    report = backfill_costs(settings, apply=True)
    assert report["eligible"] == 1 and report["backup"]
    backup = sqlite3.connect(report["backup"])
    assert json.loads(backup.execute("select data from attempts").fetchone()[0]) == before
    backup.close()
    store = Store(settings.database, settings.artifacts)
    after = store.attempt(before["id"])
    assert after["cost_usd"] == 0.1156772 and after["cost_estimate"]["backfilled"]
    assert store.run(run["id"]) == run
    for key in ("score", "status", "full_pass", "artifacts", "configured_model", "final_response"):
        assert after[key] == before[key]
    assert store.events(run["id"])[-1]["type"] == "attempt.cost_estimated"
    store.close()
    assert path.read_bytes() == raw
    assert backfill_costs(settings, apply=True)["eligible"] == 0
    assert len(list(tmp_path.glob("*.before-costs-*.db"))) == 1


@pytest.mark.parametrize("problem", ["tampered", "missing", "reported", "unknown_model", "missing_tokens"])
def test_backfill_does_not_invent_or_overwrite_costs(tmp_path, problem):
    settings, run, attempt, path = saved_run(tmp_path)
    store = Store(settings.database, settings.artifacts)
    if problem == "tampered":
        path.write_text(path.read_text() + "unexpected edit")
    elif problem == "missing":
        path.unlink()
    elif problem == "reported":
        attempt.update(cost_usd=0.5, cost_provenance="reported")
    elif problem == "unknown_model":
        attempt["configured_model"] = "terra"
    else:
        path.write_text(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10}}))
        attempt["artifacts"] = [store.register_artifact(attempt, "stdout", path)]
    store.save_attempt(attempt)
    store.close()
    assert backfill_costs(settings, apply=True)["eligible"] == 0
    store = Store(settings.database, settings.artifacts)
    assert store.attempt(attempt["id"]) == attempt
    store.close()


def test_current_profiles_and_rates():
    settings = Settings(ROOT / "routebench.example.yaml")
    assert "codex-gpt6-luna" not in settings.profiles
    assert settings.profiles["codex-luna"]["model"] == "gpt-5.6-luna"
    codex = [p for p in settings.profiles.values() if p["adapter"] == "codex"]
    assert len(codex) == 6 and all(p["pricing"]["source"] for p in codex)


def test_estimates_agree_in_summary_inspector_and_exports(tmp_path):
    import csv
    import io

    settings, run, attempt, _ = saved_run(tmp_path)
    store = Store(settings.database, settings.artifacts)
    run.update(
        security_blocked=False,
        suite={"id": "python-smoke", "cases": [{"id": "RB-PY-001", "category": "bug-fix"}]},
        attempts_per_case=1,
    )
    store.save_run(run)
    store.close()
    backfill_costs(settings, apply=True)
    app_settings = Settings(ROOT / "routebench.example.yaml")
    app_settings.database, app_settings.artifacts = settings.database, settings.artifacts
    app_settings.workspaces = tmp_path / "workspaces"
    with TestClient(create_app(app_settings)) as client:
        summary = client.get(f"/api/v1/runs/{run['id']}").json()
        detail = client.get(f"/api/v1/attempts/{attempt['id']}").json()
        exported = client.get(f"/api/v1/runs/{run['id']}/export.json").json()
        row = summary["leaderboard"][0]
        assert row["total_cost_usd"] == row["median_cost_usd"] == detail["cost_usd"] == 0.1156772
        assert row["cost_provenance"] == "estimated" and row["cost_sources"] == [PRICING]
        assert exported["attempt_history"][0]["cost_estimate"] == detail["cost_estimate"]
        for table in ("leaderboard", "attempts"):
            csv_text = client.get(f"/api/v1/runs/{run['id']}/export.csv?table={table}").text
            data = list(csv.DictReader(io.StringIO(csv_text)))[0]
            assert data["cost_provenance"] == "estimated"
            if table == "attempts":
                assert json.loads(data["cost_estimate"])["pricing"] == PRICING
            else:
                assert json.loads(data["cost_sources"]) == [PRICING]
