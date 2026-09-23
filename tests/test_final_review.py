"""Offline regressions for candidate safety, finalization, and thread ownership."""

import asyncio
import hashlib
import json
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi.testclient import TestClient
from routebench import grading, workspace
from routebench.api import RunRequest, create_app
from routebench.config import ROOT, Settings
from routebench.runner import Runner
from routebench.security import owned_delete


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ROUTEBENCH_MOCK", "1")
    definition = yaml.safe_load((ROOT / "routebench.example.yaml").read_text())
    definition["suites"] = {"python-smoke": str(ROOT / "evals/python-core/suite.yaml") + "#smoke"}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(definition))
    return Settings(path)


@pytest.fixture
async def runner(settings):
    instance = Runner(settings)
    try:
        yield instance
    finally:
        await instance.close()


async def queued_run(runner, monkeypatch):
    request = RunRequest(suite_id="python-smoke", profile_ids=["mock-baseline"]).model_dump()
    with monkeypatch.context() as patch:
        patch.setattr(runner, "start", lambda run_id, jobs: None)
        created = await runner.create(request)
    run = runner.store.run(created["id"])
    runner.cancels[run["id"]] = asyncio.Event()
    return run, runner.store.attempts(run["id"])[0]


def offline_profile(run):
    run["profiles"][0].update(adapter="custom-jsonl", model="offline", command=["offline-agent", "{prompt}"])


def passing_grade():
    return {"score": 1.0, "full_pass": True, "infrastructure_error": False, "graders": []}


@pytest.mark.parametrize("timed_out", [False, True])
async def test_unsafe_candidate_symlink_is_scored_failure(runner, monkeypatch, tmp_path, timed_out):
    run, attempt = await queued_run(runner, monkeypatch)
    offline_profile(run)
    outside = tmp_path / "outside.txt"
    outside.write_text("Untouched external fixture")
    candidate = runner.settings.workspaces / f"{run['id']}--{attempt['id']}"

    async def unsafe_output(command, path, environment, timeout, cancel, stdout, stderr, on_line, *args):
        (path / "unsafe.txt").symlink_to(outside)
        stdout.write_text("")
        stderr.write_text("")
        await on_line(json.dumps({"type": "agent.finished", "text": "Offline completion"}))
        return {"exit_code": -15 if timed_out else 0, "agent_duration_ms": 1, "timed_out": timed_out}

    grade = AsyncMock(return_value=passing_grade())
    monkeypatch.setattr("routebench.runner.supervise", unsafe_output)
    monkeypatch.setattr(grading, "grade", grade)
    await runner.execute_attempt(run, attempt, runner.cancels[run["id"]])
    persisted = runner.store.attempt(attempt["id"])
    assert persisted["status"] == ("timed_out" if timed_out else "failed"), persisted
    assert persisted["score"] == 0.0
    assert persisted["full_pass"] is False
    assert persisted["error_code"] == ("timeout" if timed_out else "invalid_candidate_output")
    assert persisted["graders"][0]["id"] == "workspace_safety"
    assert persisted["graders"][0]["infrastructure_error"] is False
    grade.assert_not_awaited()
    assert outside.read_text() == "Untouched external fixture"
    assert not candidate.exists()
    assert candidate not in workspace._BASELINES


async def test_artifact_registration_failure_cleans_workspace_and_halts_schedule(runner, monkeypatch):
    run, first = await queued_run(runner, monkeypatch)
    prepared = []
    real_prepare = workspace.prepare

    def record_prepare(*args):
        path = real_prepare(*args)
        prepared.append(path)
        return path

    def fail_registration(*args):
        raise OSError("Synthetic artifact registration failure")

    monkeypatch.setattr(workspace, "prepare", record_prepare)
    monkeypatch.setattr(grading, "grade", AsyncMock(return_value=passing_grade()))
    monkeypatch.setattr(runner.store, "register_artifact", fail_registration)
    await asyncio.wait_for(runner.execute(run["id"], runner.store.attempts(run["id"])), 15)
    assert runner.fatal_error == "Synthetic artifact registration failure"
    assert runner.cancels[run["id"]].is_set()
    assert runner.store.run(run["id"])["status"] == "failed"
    assert len(prepared) == 1
    assert not prepared[0].exists()
    assert prepared[0] not in workspace._BASELINES
    assert not list(runner.settings.workspaces.iterdir())
    remaining = [a for a in runner.store.attempts(run["id"]) if a["id"] != first["id"]]
    assert remaining and all(a["status"] == "cancelled" for a in remaining)
    assert all(a["started_at"] is None for a in remaining)


@pytest.mark.parametrize("operation", ["prepare", "capture"])
async def test_cancellation_waits_for_filesystem_thread_without_orphans(runner, monkeypatch, operation):
    run, attempt = await queued_run(runner, monkeypatch)
    candidate = runner.settings.workspaces / f"{run['id']}--{attempt['id']}"
    started = asyncio.Event()
    release_thread = threading.Event()
    finished = threading.Event()
    loop = asyncio.get_running_loop()
    real_operation = getattr(workspace, operation)

    def blocked_operation(*args):
        result = real_operation(*args) if operation == "prepare" else None
        loop.call_soon_threadsafe(started.set)
        try:
            assert release_thread.wait(10), "Test did not release filesystem worker"
            assert candidate.is_dir(), "Candidate was deleted while its worker was still active"
            return result if operation == "prepare" else real_operation(*args)
        finally:
            finished.set()

    def ordered_delete(root, path):
        assert finished.is_set(), "Cleanup raced the filesystem worker"
        owned_delete(root, path)

    monkeypatch.setattr(workspace, operation, blocked_operation)
    monkeypatch.setattr("routebench.runner.owned_delete", ordered_delete)
    grade = AsyncMock(return_value=passing_grade())
    monkeypatch.setattr(grading, "grade", grade)
    task = asyncio.create_task(runner.execute_attempt(run, attempt, runner.cancels[run["id"]]))
    try:
        await asyncio.wait_for(started.wait(), 10)
        task.cancel()
        # Yield a scheduler turn so cancellation is delivered while the worker is gated.
        await asyncio.sleep(0)
        assert not task.done()
        assert candidate.is_dir()
        release_thread.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
        assert finished.is_set()
        assert not candidate.exists()
        assert candidate not in workspace._BASELINES
        assert not list(runner.settings.workspaces.iterdir())
        assert runner.store.attempt(attempt["id"])["error_code"] == "interrupted"
        grade.assert_not_awaited()
    finally:
        release_thread.set()
        if not task.done() and not task.cancelling():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.to_thread(finished.wait, 10)
        if candidate.exists():
            owned_delete(runner.settings.workspaces, candidate)
        workspace.release(candidate)


async def test_artifact_api_scrubs_legacy_paths_and_new_raw_hashes_match(settings, monkeypatch):
    runner = Runner(settings)
    try:
        run, attempt = await queued_run(runner, monkeypatch)
        offline_profile(run)

        async def path_output(command, path, environment, timeout, cancel, stdout, stderr, on_line, *args):
            text = f"candidate={path}/example.py\nhome={Path.home()}/example.txt\n"
            stdout.write_text(text)
            stderr.write_text(text)
            (path / "path-evidence.txt").write_text(text)
            await on_line(json.dumps({"type": "agent.finished", "text": "Offline completion"}))
            return {"exit_code": 0, "agent_duration_ms": 1}

        monkeypatch.setattr("routebench.runner.supervise", path_output)
        monkeypatch.setattr(grading, "grade", AsyncMock(return_value=passing_grade()))
        await runner.execute_attempt(run, attempt, runner.cancels[run["id"]])
        persisted = runner.store.attempt(attempt["id"])
        assert persisted["status"] == "passed", persisted
        raw_artifacts = [a for a in persisted["artifacts"] if a["kind"] in {"stdout", "stderr", "diff"}]
        assert len(raw_artifacts) == 3
        for artifact in raw_artifacts:
            metadata, path = runner.store.artifact(artifact["id"])
            content = path.read_bytes()
            assert str(Path.home()).encode() not in content
            assert str(settings.workspaces).encode() not in content
            assert b"<workspace>/example.py" in content
            assert metadata["sha256"] == hashlib.sha256(content).hexdigest()
            assert metadata["byte_size"] == len(content)

        legacy_attempt = runner.store.attempts(run["id"])[1]
        legacy_path = runner.store.artifact_dir(run["id"], legacy_attempt["id"]) / "patch.diff"
        legacy_text = f"old={settings.workspaces}/old-candidate/source.py\nhome={Path.home()}/old.txt\n"
        legacy_path.write_text(legacy_text)
        legacy = runner.store.register_artifact(legacy_attempt, "diff", legacy_path)
        legacy_attempt["artifacts"].append(legacy)
        runner.store.save_attempt(legacy_attempt)
    finally:
        await runner.close()

    with TestClient(create_app(settings)) as client:
        for artifact in raw_artifacts:
            response = client.get(f"/api/v1/artifacts/{artifact['id']}")
            assert response.status_code == 200
            assert hashlib.sha256(response.content).hexdigest() == artifact["sha256"]
            assert len(response.content) == artifact["byte_size"]
            if artifact["kind"] == "diff":
                diff_response = client.get(f"/api/v1/attempts/{attempt['id']}/diff")
                assert diff_response.status_code == 200
                assert diff_response.content == response.content
        for endpoint in (
            f"/api/v1/artifacts/{legacy['id']}",
            f"/api/v1/attempts/{legacy_attempt['id']}/diff",
        ):
            response = client.get(endpoint)
            assert response.status_code == 200
            assert str(Path.home()) not in response.text
            assert str(settings.workspaces) not in response.text
            assert "<workspaces>/old-candidate/source.py" in response.text
            assert "~/old.txt" in response.text
        assert legacy_path.read_text() == legacy_text
