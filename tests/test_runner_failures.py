"""Offline runner regressions using public fixtures and controlled async boundaries."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
import yaml
from fastapi.testclient import TestClient
from routebench import grading, workspace
from routebench.api import RunRequest, create_app
from routebench.config import ROOT, Settings
from routebench.runner import Runner
from routebench.security import REDACTED


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


def request():
    return RunRequest(suite_id="python-smoke", profile_ids=["mock-baseline"]).model_dump()


async def queued_run(runner, monkeypatch):
    # Create the real persisted schedule while leaving execution under test control.
    with monkeypatch.context() as patch:
        patch.setattr(runner, "start", lambda run_id, jobs: None)
        created = await runner.create(request())
    run = runner.store.run(created["id"])
    runner.cancels[run["id"]] = asyncio.Event()
    return run, runner.store.attempts(run["id"])[0]


def passing_grade():
    return {"score": 1.0, "full_pass": True, "infrastructure_error": False, "graders": []}


async def test_future_codex_attempt_persists_estimate_from_stream(runner, monkeypatch):
    run, attempt = await queued_run(runner, monkeypatch)
    profile = run["profiles"][0]
    profile.update(adapter="codex", model="gpt-5.6-terra", pricing={
        "source": "Saved test list prices", "effective_date": "2026-09-23",
        "input_per_million": 2, "cached_input_per_million": .2, "output_per_million": 12,
    })
    runner.store.save_run(run)

    async def recorded_stream(command, directory, environment, timeout, cancel, stdout, stderr, on_line, *args):
        events = [{"type": "turn.started"}, {"type": "turn.completed", "usage": {
            "input_tokens": 244045, "cached_input_tokens": 228096, "output_tokens": 3180,
            "cache_write_input_tokens": 0, "reasoning_output_tokens": 1298,
        }}]
        stdout.write_text("\n".join(json.dumps(e) for e in events))
        stderr.write_text("")
        for event in events:
            await on_line(json.dumps(event))
        return {"exit_code": 0, "agent_duration_ms": 100}

    monkeypatch.setattr("routebench.runner.supervise", recorded_stream)
    monkeypatch.setattr(grading, "grade", AsyncMock(return_value=passing_grade()))
    await runner.execute_attempt(run, attempt, runner.cancels[run["id"]])
    result = runner.store.attempt(attempt["id"])
    assert result["status"] == "passed" and result["cost_usd"] == .1156772
    assert result["cost_provenance"] == "estimated"
    assert result["cost_estimate"]["pricing"] == profile["pricing"]
    assert result["cost_estimate"]["buckets"]["input"]["tokens"] == 15949


async def test_outer_cancellation_awaits_grader_cleanup_before_workspace_removal(runner, monkeypatch):
    run, attempt = await queued_run(runner, monkeypatch)
    started = asyncio.Event()
    cleaning = asyncio.Event()
    allow_cleanup = asyncio.Event()
    cleaned = asyncio.Event()
    candidate = runner.settings.workspaces / f"{run['id']}--{attempt['id']}"

    async def blocked_grade(case, path, capture, settings):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await allow_cleanup.wait()
            assert path.is_dir()
            cleaned.set()

    monkeypatch.setattr(grading, "grade", blocked_grade)
    task = asyncio.create_task(runner.execute_attempt(run, attempt, runner.cancels[run["id"]]))
    try:
        await asyncio.wait_for(started.wait(), 10)
        task.cancel()
        await asyncio.wait_for(cleaning.wait(), 10)
        assert candidate.is_dir()
        assert not task.done()
        allow_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
        assert cleaned.is_set()
        assert not candidate.exists()
        persisted = runner.store.attempt(attempt["id"])
        assert persisted["status"] == "infrastructure_error"
        assert persisted["score"] is None
        assert persisted["error_code"] == "interrupted"
    finally:
        allow_cleanup.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_explicit_environment_secret_in_grading_blocks_exports(settings, monkeypatch):
    secret = "synthetic-explicit-environment-credential"
    monkeypatch.setenv("ROUTEBENCH_TEST_API_KEY", secret)
    runner = Runner(settings)
    try:
        run, attempt = await queued_run(runner, monkeypatch)
        run["profiles"][0]["env_allowlist"] = ["ROUTEBENCH_TEST_API_KEY"]
        runner.store.save_run(run)

        async def secret_grade(*args):
            return {**passing_grade(), "graders": [{"id": "synthetic", "output": secret}]}

        monkeypatch.setattr(grading, "grade", secret_grade)
        await runner.execute_attempt(run, attempt, runner.cancels[run["id"]])
        persisted = runner.store.attempt(attempt["id"])
        assert persisted["graders"][0]["output"] == REDACTED
        assert persisted["status"] == "cancelled"
        assert persisted["error_code"] == "security_blocked"
        assert runner.store.run(run["id"])["security_blocked"]
        assert runner.cancels[run["id"]].is_set()
        assert secret not in json.dumps(persisted)
        assert secret not in json.dumps(runner.store.events(run["id"]))
        for artifact in persisted["artifacts"]:
            _, path = runner.store.artifact(artifact["id"])
            assert secret not in path.read_text()
    finally:
        await runner.close()

    with TestClient(create_app(settings)) as client:
        for extension in ("json", "csv"):
            response = client.get(f"/api/v1/runs/{run['id']}/export.{extension}")
            assert response.status_code == 409
            assert secret not in response.text


async def test_recovery_cancels_queued_replacement_under_terminal_run_and_preserves_lineage(
    settings, monkeypatch
):
    runner = Runner(settings)
    try:
        run, original = await queued_run(runner, monkeypatch)
        for attempt in runner.store.attempts(run["id"]):
            attempt.update(status="cancelled", error_code="interrupted")
            runner.store.save_attempt(attempt)
        original.update(status="infrastructure_error", error_code="interrupted")
        runner.store.save_attempt(original)
        replacement = runner._attempt(
            run["id"],
            original["case_id"],
            original["profile_id"],
            original["attempt_index"],
            original["execution_order"],
        )
        replacement["supersedes_attempt_id"] = original["id"]
        runner.store.save_attempt(replacement)
        run["status"] = "completed_with_errors"
        runner.store.save_run(run)
    finally:
        await runner.close()

    recovered = Runner(settings)
    try:
        pending = recovered.store.attempt(replacement["id"])
        assert pending["status"] == "cancelled"
        assert pending["error_code"] == "restart"
        assert pending["score"] is None
        assert recovered.store.attempt(original["id"])["status"] == "infrastructure_error"
        assert not recovered.active()
        assert original["id"] not in {a["id"] for a in recovered.store.attempts(run["id"])}
        monkeypatch.setattr(recovered, "start", lambda run_id, jobs: None)
        rerun = await recovered.rerun(replacement["id"])
        newest = recovered.store.attempt(rerun["id"])
        assert newest["supersedes_attempt_id"] == replacement["id"]
        assert newest["attempt_index"] == original["attempt_index"]
        assert newest["execution_order"] == original["execution_order"]
        assert recovered.store.run(run["id"])["status"] == "queued"
        effective_ids = {a["id"] for a in recovered.store.attempts(run["id"])}
        assert newest["id"] in effective_ids
        assert not {original["id"], replacement["id"]} & effective_ids
        assert len(recovered.store.attempts(run["id"], effective=False)) == run["total_attempts"] + 2
    finally:
        await recovered.close()


@pytest.mark.parametrize("limits", [(17, 33, 55), (55, 17, 33), (33, 55, 17)])
async def test_effective_timeout_is_minimum_and_passed_to_supervisor(runner, monkeypatch, limits):
    run, attempt = await queued_run(runner, monkeypatch)
    run["timeout_seconds"], case_limit, profile_limit = limits
    case = next(c for c in run["_suite"]["cases"] if c["id"] == attempt["case_id"])
    case["timeout_seconds"] = case_limit
    run["profiles"][0].update(
        adapter="custom-jsonl",
        model="offline",
        command=["offline-agent", "{prompt}"],
        timeout_seconds=profile_limit,
    )
    passed_timeouts = []

    async def fake_supervise(command, path, environment, timeout, cancel, stdout, stderr, on_line, *args):
        passed_timeouts.append(timeout)
        assert runner.store.attempt(attempt["id"])["effective_timeout_seconds"] == min(limits)
        stdout.write_text("")
        stderr.write_text("")
        await on_line(json.dumps({"type": "agent.finished", "text": "Offline completion"}))
        return {"exit_code": 0, "agent_duration_ms": 1}

    monkeypatch.setattr("routebench.runner.supervise", fake_supervise)
    monkeypatch.setattr(grading, "grade", AsyncMock(return_value=passing_grade()))
    await runner.execute_attempt(run, attempt, runner.cancels[run["id"]])
    persisted = runner.store.attempt(attempt["id"])
    assert persisted["status"] == "passed", persisted
    assert persisted["effective_timeout_seconds"] == min(limits)
    assert passed_timeouts == [min(limits)]


async def test_patch_artifact_reports_writer_truncation(runner, monkeypatch):
    run, attempt = await queued_run(runner, monkeypatch)
    runner.settings.execution["max_artifact_bytes"] = 256
    real_capture = workspace.capture
    patch_text = "diff --git a/example.py b/example.py\n" + "+example = 1\n" * 100

    def large_capture(path):
        return {**real_capture(path), "diff": patch_text, "diff_truncated": False}

    async def inspect_grade(case, path, capture, settings):
        assert capture["diff"] == patch_text
        return passing_grade()

    monkeypatch.setattr(workspace, "capture", large_capture)
    monkeypatch.setattr(grading, "grade", inspect_grade)
    await runner.execute_attempt(run, attempt, runner.cancels[run["id"]])
    persisted = runner.store.attempt(attempt["id"])
    assert persisted["status"] == "passed", persisted
    artifact = next(a for a in persisted["artifacts"] if a["kind"] == "diff")
    assert artifact["truncated"] is True
    metadata, path = runner.store.artifact(artifact["id"])
    assert metadata["truncated"] is True
    assert path.stat().st_size < len(patch_text.encode())
    assert path.read_text().startswith("diff --git")


async def test_concurrent_creation_starts_only_one_run(runner, monkeypatch):
    first_preflight = asyncio.Event()
    release_preflight = asyncio.Event()
    second_started = asyncio.Event()
    real_preflight = runner.preflight
    calls = []

    async def blocked_preflight():
        calls.append(True)
        first_preflight.set()
        await release_preflight.wait()
        return await real_preflight()

    async def blocked_execution(run_id, jobs):
        await runner.cancels[run_id].wait()

    async def second_create():
        second_started.set()
        return await runner.create(request())

    monkeypatch.setattr(runner, "preflight", blocked_preflight)
    monkeypatch.setattr(runner, "execute", blocked_execution)
    first = asyncio.create_task(runner.create(request()))
    second = None
    try:
        await asyncio.wait_for(first_preflight.wait(), 10)
        second = asyncio.create_task(second_create())
        await asyncio.wait_for(second_started.wait(), 10)
        release_preflight.set()
        results = await asyncio.wait_for(asyncio.gather(first, second, return_exceptions=True), 10)
        assert sum(isinstance(result, dict) for result in results) == 1
        rejected = next(result for result in results if isinstance(result, ValueError))
        assert "already active" in str(rejected)
        assert len(calls) == 1
        assert len(runner.store.runs()) == 1
        assert sum(not task.done() for task in runner.tasks.values()) == 1
    finally:
        release_preflight.set()
        for task in (first, second):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(*(task for task in (first, second) if task is not None), return_exceptions=True)


async def test_fatal_storage_error_prevents_rerun(runner, monkeypatch):
    run, attempt = await queued_run(runner, monkeypatch)
    attempt["status"] = "infrastructure_error"
    runner.store.save_attempt(attempt)
    runner.fatal_error = "Synthetic storage failure"
    preflight = AsyncMock()
    monkeypatch.setattr(runner, "preflight", preflight)
    before = runner.store.attempts(run["id"], effective=False)
    with pytest.raises(ValueError, match="Storage failure"):
        await runner.rerun(attempt["id"])
    preflight.assert_not_awaited()
    assert runner.store.attempts(run["id"], effective=False) == before
    assert not runner.active()
