import asyncio
import sys
import time
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from routebench.api import create_app
from routebench.config import ROOT, Settings
from routebench.process import supervise
from routebench.storage import Store

HEADERS = {"X-RouteBench-Request": "1"}


def config(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEBENCH_MOCK", "1")
    definition = yaml.safe_load((ROOT / "routebench.example.yaml").read_text())
    manifest = str(ROOT / "evals/python-core/suite.yaml")
    definition["suites"] = {
        "python-smoke": manifest + "#smoke",
        "python-core": manifest,
        "python-planning": manifest + "#planning",
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(definition))
    return Settings(path)


def wait_run(client, run_id):
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.05)
    pytest.fail("Mock run did not finish")


def test_browser_workflow_and_replay(tmp_path, monkeypatch):
    settings = config(tmp_path, monkeypatch)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/profiles").json()["profiles"][0]["preflight"]["status"] == "ready"
        payload = {
            "suite_id": "python-smoke",
            "profile_ids": ["mock-reference", "mock-baseline"],
            "keep_workspaces": True,
        }
        assert client.post("/api/v1/runs", json=payload).status_code == 403
        assert (
            client.post(
                "/api/v1/runs", json=payload, headers={**HEADERS, "Origin": "https://evil.example"}
            ).status_code
            == 403
        )
        response = client.post("/api/v1/runs", json=payload, headers=HEADERS)
        assert response.status_code == 201, response.text
        run_id = response.json()["id"]
        run = wait_run(client, run_id)
        assert run["status"] == "completed", run
        assert run["completed_attempts"] == 6
        assert [a["status"] for a in run["attempts"]].count("passed") == 3
        row = next(r for r in run["leaderboard"] if r["profile_id"] == "mock-reference")
        assert row["mean_score"] == 1.0
        assert row["total_cost_usd"] is None
        attempt = next(a for a in run["attempts"] if a["status"] == "passed")
        detail = client.get(f"/api/v1/attempts/{attempt['id']}").json()
        assert detail["graders"] and detail["fixture_hash"]
        assert "diff --git" in client.get(f"/api/v1/attempts/{attempt['id']}/diff").text
        assert (settings.workspaces / f"{run_id}--{attempt['id']}").is_dir()
        exported = client.get(f"/api/v1/runs/{run_id}/export.json")
        assert exported.status_code == 200
        assert str(Path.home()) not in exported.text
        assert "def test_" not in exported.text
        assert "cost_provenance" in client.get(f"/api/v1/runs/{run_id}/export.csv?table=attempts").text
        stream = client.get(f"/api/v1/runs/{run_id}/events", headers={"Last-Event-ID": "1"}).text
        assert "id: 1\n" not in stream and "event: run.completed" in stream
        assert client.get("/api/v1/artifacts/not-owned").status_code == 404
    with TestClient(create_app(settings)) as client:
        assert client.get(f"/api/v1/runs/{run_id}").json()["completed_attempts"] == 6
        assert client.delete(f"/api/v1/runs/{run_id}", headers=HEADERS).status_code == 200
        assert not (settings.workspaces / f"{run_id}--{attempt['id']}").exists()
        assert client.get(f"/api/v1/runs/{run_id}").status_code == 404


def test_planning_suite_and_offline_evidence(tmp_path, monkeypatch):
    settings = config(tmp_path, monkeypatch)
    with TestClient(create_app(settings)) as client:
        suites = {s["id"]: s for s in client.get("/api/v1/suites").json()["suites"]}
        assert len(suites["python-core"]["cases"]) == 9
        assert len(suites["python-smoke"]["cases"]) == 3
        planning = suites["python-planning"]
        assert planning["name"] == "RouteBench Python Core · Planning"
        assert planning["description"].startswith("1 task selected")
        assert planning["version"] == "1.1.0"
        assert [c["id"] for c in planning["cases"]] == ["RB-PY-009"]
        assert planning["cases"][0] == suites["python-core"]["cases"][-1]
        response = client.post(
            "/api/v1/runs",
            json={"suite_id": "python-planning", "profile_ids": ["mock-reference", "mock-baseline"]},
            headers=HEADERS,
        )
        assert response.status_code == 201, response.text
        run = wait_run(client, response.json()["id"])
        assert run["status"] == "completed", run
        assert run["total_attempts"] == run["completed_attempts"] == 2
        attempts = {a["profile_id"]: a for a in run["attempts"]}
        reference = attempts["mock-reference"]
        assert reference["status"] == "passed" and reference["score"] == 1.0
        assert attempts["mock-baseline"]["status"] == "failed"
        detail = client.get(f"/api/v1/attempts/{reference['id']}").json()
        assert all(g["passed"] for g in detail["graders"])
        diff = client.get(f"/api/v1/attempts/{reference['id']}/diff").text
        for name in ("PLAN.md", "VERIFICATION.md", "tests/test_reservations.py"):
            assert f"b/{name}" in diff
        assert "__hidden__" not in diff
        exported = client.get(f"/api/v1/runs/{run['id']}/export.json").json()
        saved = next(a for a in exported["attempt_history"] if a["id"] == reference["id"])
        assert saved["score"] == 1.0
        assert any(a["kind"] == "diff" for a in saved["artifacts"])
        csv_text = client.get(f"/api/v1/runs/{run['id']}/export.csv?table=attempts").text
        assert "RB-PY-009" in csv_text and "mock-reference" in csv_text


def test_cancel_preserves_evidence(tmp_path, monkeypatch):
    settings = config(tmp_path, monkeypatch)
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/runs",
            json={"suite_id": "python-smoke", "profile_ids": ["mock-reference"], "attempts_per_case": 3},
            headers=HEADERS,
        )
        run_id = response.json()["id"]
        assert client.post(f"/api/v1/runs/{run_id}/cancel", headers=HEADERS).status_code == 200
        run = wait_run(client, run_id)
        assert run["status"] == "cancelled"
        assert all(a["status"] in {"cancelled", "passed"} for a in run["attempts"])


def test_restart_marks_interrupted_invalid(tmp_path):
    store = Store(tmp_path / "db.sqlite", tmp_path / "artifacts")
    store.save_run({"id": "r", "status": "running", "warnings": []})
    store.save_attempt({"id": "a", "run_id": "r", "status": "running"})
    store.save_attempt({"id": "b", "run_id": "r", "status": "queued"})
    store.recover()
    assert store.attempt("a")["score"] is None
    assert store.attempt("a")["status"] == "infrastructure_error"
    assert store.attempt("b")["status"] == "cancelled"
    assert store.run("r")["status"] == "completed_with_errors"
    store.close()


def test_database_has_one_scheduler_owner(tmp_path):
    store = Store(tmp_path / "db.sqlite", tmp_path / "artifacts")
    with pytest.raises(ValueError, match="already in use"):
        Store(tmp_path / "db.sqlite", tmp_path / "artifacts")
    store.close()
    other = Store(tmp_path / "db.sqlite", tmp_path / "artifacts")
    other.close()


@pytest.mark.asyncio
async def test_supervisor_timeout_drain_and_redaction(tmp_path):
    cancel = asyncio.Event()
    captured = []

    async def line(value, stderr):
        captured.append(value)

    script = "import sys,time,subprocess; subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)']); print('started',flush=True); sys.stderr.write('x'*100000); sys.stderr.flush(); time.sleep(30)"
    result = await supervise(
        [sys.executable, "-c", script],
        tmp_path,
        {"PATH": "/usr/bin:/bin"},
        0.3,
        cancel,
        tmp_path / "out",
        tmp_path / "err",
        line,
        20000,
    )
    assert result["timed_out"]
    assert result["agent_duration_ms"] < 4000
    assert result["stderr_truncated"]
    assert "started" in captured
    cancel = asyncio.Event()
    script = "import sys,time;sys.stdout.write('abc-secret-');sys.stdout.flush();time.sleep(.03);print('value-xyz',flush=True);time.sleep(30)"
    result = await supervise(
        [sys.executable, "-c", script],
        tmp_path,
        {},
        3,
        cancel,
        tmp_path / "out2",
        tmp_path / "err2",
        line,
        20000,
        ["abc-secret-value-xyz"],
    )
    assert result["secret_detected"]
    assert "abc-secret" not in (tmp_path / "out2").read_text()
