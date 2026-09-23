import asyncio
import json
import os
import random
import sys
import time
import uuid
from copy import deepcopy
from pathlib import Path

from . import adapters
from .config import fingerprint
from .costs import estimate_cost
from .metrics import aggregate, router_summary
from .process import BoundedArtifact, supervise
from .security import (
    REDACTED,
    SECRET_NAME,
    StreamRedactor,
    clean_environment,
    owned_delete,
    safe_child,
    sanitize,
)
from .storage import Store, now

TERMINAL = {"passed", "failed", "timed_out", "adapter_error", "infrastructure_error", "cancelled"}
RUN_TERMINAL = {"completed", "completed_with_errors", "cancelled", "failed"}
SUFFIX = "\n\nWork only in the current repository. Implement the requested change and run relevant tests if possible. Do not ask for interactive input."


def new_id(prefix):
    return prefix + "_" + uuid.uuid4().hex[:16]


class Runner:
    def __init__(self, settings):
        from .grading import cleanup_stale

        self.settings = settings
        self.store = Store(settings.database, settings.artifacts)
        settings.workspaces.mkdir(parents=True, exist_ok=True, mode=0o700)
        cleanup_stale(settings)
        self.store.recover()
        self.tasks = {}
        self.cancels = {}
        self.preflights = {}
        self.fatal_error = None
        self.creation_lock = asyncio.Lock()
        self.prune()

    def prune(self):
        days = self.settings.storage["retention_days"]
        if not isinstance(days, int) or days < 1:
            raise ValueError("retention_days must be a positive integer")
        cutoff = time.time() - days * 86400
        for run in self.store.runs():
            if run["status"] not in RUN_TERMINAL:
                continue
            directory = safe_child(self.settings.artifacts, run["id"])
            if directory.exists() and directory.stat().st_mtime < cutoff:
                owned_delete(self.settings.artifacts, directory)
                run["warnings"].append("Artifact retention period elapsed; summaries remain available.")
                self.store.save_run(run)

    async def preflight(self):
        async def check(profile):
            if profile["adapter"] == "mock":
                result = {
                    "profile_id": profile["id"],
                    "status": "ready",
                    "version": "test-only",
                    "authentication": "not_checked",
                    "structured_output": True,
                    "warnings": ["Simulated local execution; no model is called."],
                    "capabilities": {
                        "usage": False,
                        "reported_cost": False,
                        "route": False,
                        "tool_events": True,
                    },
                }
            else:
                result = await adapters.preflight(profile)
            if not profile["enabled"]:
                result["status"] = "blocked"
                result["warnings"].append("Profile disabled in YAML.")
            sensitive = [name for name in profile["env_allowlist"] if SECRET_NAME.search(name)]
            if sensitive:
                result["warnings"].append(
                    "Repository commands can access explicitly allowed sensitive environment variables: "
                    + ", ".join(sensitive)
                )
            if profile["adapter"] == "pi":
                result["warnings"].append(
                    "Pi runs with your user permissions. Temporary copies are not a security sandbox."
                )
            observation = self.store.latest_profile_result(profile["id"], profile["model"])
            result["model_access"] = "not_checked"
            if observation:
                state, error = observation
                if state in {"passed", "failed"}:
                    result["model_access"] = "verified_in_previous_run"
                elif error:
                    result["model_access"] = "last_execution_failed"
                    result["warnings"].append("Last execution: " + str(sanitize(error))[:500])
            return profile["id"], result

        self.preflights = dict(await asyncio.gather(*(check(p) for p in self.settings.profiles.values())))
        return self.profiles()

    def profiles(self):
        return [
            sanitize({**p, "preflight": self.preflights.get(p["id"])})
            for p in self.settings.profiles.values()
        ]

    def active(self):
        return any(not t.done() for t in self.tasks.values())

    def _attempt(self, run_id, case_id, profile_id, repeat, order):
        return {
            "id": new_id("att"),
            "run_id": run_id,
            "case_id": case_id,
            "profile_id": profile_id,
            "attempt_index": repeat,
            "execution_order": order,
            "status": "queued",
            "score": None,
            "full_pass": None,
            "agent_duration_ms": None,
            "preparation_duration_ms": None,
            "grading_duration_ms": None,
            "input_tokens": None,
            "output_tokens": None,
            "cached_input_tokens": None,
            "reasoning_tokens": None,
            "cost_usd": None,
            "cost_provenance": "unavailable",
            "tool_calls": None,
            "turns": None,
            "retries": None,
            "primary_observed_model": None,
            "exit_code": None,
            "error_code": None,
            "error_message": None,
            "files_changed": None,
            "lines_added": None,
            "lines_removed": None,
            "created_at": now(),
            "started_at": None,
            "completed_at": None,
            "final_response": "",
            "graders": [],
            "routes": [],
            "artifacts": [],
            "workspace_retained": False,
        }

    async def create(self, request):
        async with self.creation_lock:
            return await self._create(request)

    async def _create(self, request):
        if self.active():
            raise ValueError("A run is already active. Wait for it to finish or cancel it.")
        if self.fatal_error:
            raise ValueError("Storage failure: restart RouteBench after repairing local storage.")
        if request["suite_id"] not in self.settings.suites:
            raise ValueError("Unknown suite")
        if not request["profile_ids"] or len(set(request["profile_ids"])) != len(request["profile_ids"]):
            raise ValueError("Choose distinct profiles")
        if any(p not in self.settings.profiles for p in request["profile_ids"]):
            raise ValueError("Unknown profile")
        await self.preflight()
        if any(self.preflights[p]["status"] != "ready" for p in request["profile_ids"]):
            raise ValueError("Selected profiles did not pass preflight")
        suite = self.settings.load_suite(request["suite_id"], self.settings.suite_specs[request["suite_id"]])
        profiles = [
            {**deepcopy(self.settings.profiles[p]), "preflight": self.preflights[p]}
            for p in request["profile_ids"]
        ]
        ident = new_id("run")
        warnings = [
            "Configuration comparison: results include CLI tools, permissions, plugins, and model behavior."
        ]
        if request["mode"] == "parallel":
            warnings.append(
                "Parallel execution includes laptop contention; latency is not directly comparable to sequential runs."
            )
        if any(p["adapter"] == "pi" for p in profiles):
            warnings.append(
                "Trusted fixtures only: Pi tools are not confined by the disposable repository copy."
            )
        run = {
            "id": ident,
            "status": "queued",
            "suite": self.settings.public_suite(suite),
            "_suite": suite,
            "profiles": profiles,
            **request,
            "created_at": now(),
            "started_at": None,
            "completed_at": None,
            "completed_attempts": 0,
            "warnings": warnings,
            "config_fingerprint": self.settings.fingerprint,
            "host_fingerprint": {"platform": sys.platform, "python": sys.version.split()[0]},
            "prompt_suffix_version": 1,
            "security_blocked": False,
        }
        rng = random.Random(request["random_seed"])
        jobs = []
        for case in suite["cases"]:
            for repeat in range(1, request["attempts_per_case"] + 1):
                order = list(request["profile_ids"])
                rng.shuffle(order)
                for profile in order:
                    jobs.append(self._attempt(ident, case["id"], profile, repeat, len(jobs) + 1))
        if len(jobs) > 500:
            raise ValueError("A run may contain at most 500 attempts")
        run["total_attempts"] = len(jobs)
        self.store.save_run(run)
        for job in jobs:
            self.store.save_attempt(job)
        self.start(ident, jobs)
        return {
            "id": ident,
            "total_attempts": len(jobs),
            "warnings": warnings,
            "events_url": f"/api/v1/runs/{ident}/events",
        }

    def start(self, run_id, jobs):
        self.cancels[run_id] = asyncio.Event()
        self.tasks[run_id] = asyncio.create_task(self.execute(run_id, jobs))

    async def execute(self, run_id, jobs):
        cancel = self.cancels[run_id]
        run = self.store.run(run_id)
        run.update(status="running", started_at=run.get("started_at") or now(), completed_at=None)
        self.store.save_run(run, "run.started")
        concurrency = min(2, self.settings.execution["max_concurrency"]) if run["mode"] == "parallel" else 1
        semaphore = asyncio.Semaphore(concurrency)

        async def work(job):
            async with semaphore:
                if cancel.is_set():
                    job.update(status="cancelled", completed_at=now())
                    self.store.save_attempt(job, "attempt.status")
                    return
                try:
                    await self.execute_attempt(run, job, cancel)
                except Exception:
                    cancel.set()
                    raise

        tasks = [asyncio.create_task(work(job)) for job in jobs]
        try:
            await asyncio.gather(*tasks)
            run = self.store.run(run_id)
            attempts = self.store.attempts(run_id)
            if run.get("security_blocked"):
                run["status"] = "failed"
            elif cancel.is_set():
                run["status"] = "cancelled"
            elif any(a["status"] in {"infrastructure_error", "adapter_error", "timed_out"} for a in attempts):
                run["status"] = "completed_with_errors"
            else:
                run["status"] = "completed"
            run.update(completed_at=now(), completed_attempts=sum(a["status"] in TERMINAL for a in attempts))
            self.store.save_run(run, "run.completed")
        except asyncio.CancelledError:
            cancel.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        except Exception as exc:
            cancel.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.fatal_error = sanitize(str(exc))
            try:
                run.update(status="failed", completed_at=now())
                run["warnings"].append("Local storage or scheduler failed. New scheduling has stopped.")
                self.store.save_run(run, "run.completed")
            except Exception as storage_exc:
                print(
                    "RouteBench could not persist the failure: " + str(sanitize(str(storage_exc))),
                    file=sys.stderr,
                )

    def incident(self, run_id, attempt_id):
        self.cancels[run_id].set()
        run = self.store.run(run_id)
        run["security_blocked"] = True
        run["warnings"].append(
            f"Possible secret detected in {attempt_id}; redacted, execution stopped, and exports blocked. Review local configuration before a new run."
        )
        self.store.save_run(run, "run.warning")

    async def execute_attempt(self, run, attempt, cancel):
        from . import workspace as ws
        from .grading import grade

        profile = next(p for p in run["profiles"] if p["id"] == attempt["profile_id"])
        case = next(c for c in run["_suite"]["cases"] if c["id"] == attempt["case_id"])
        attempt.update(
            status="preparing",
            started_at=now(),
            configured_model=profile["model"],
            fixture_hash=case["fixture_hash"],
            prompt_hash=fingerprint(case["prompt"] + SUFFIX),
            prompt=case["prompt"] + SUFFIX,
            effective_timeout_seconds=min(
                run["timeout_seconds"], case.get("timeout_seconds", 900), profile.get("timeout_seconds", 900)
            ),
        )
        self.store.save_attempt(attempt, "attempt.status")
        preparation = time.monotonic()
        directory = self.store.artifact_dir(run["id"], attempt["id"])
        normalized_path = directory / "events.jsonl"
        normalized = BoundedArtifact(normalized_path, self.settings.execution["max_artifact_bytes"])
        environment = clean_environment(profile.get("env_allowlist", []))
        # The same already-installed Python test tools are available to every profile.
        environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + environment.get("PATH", "")
        secrets = [
            environment[k]
            for k in profile.get("env_allowlist", [])
            if k in environment and SECRET_NAME.search(k)
        ]
        sequence = 0
        workspace = None
        process_result = {}
        try:
            preparing = asyncio.create_task(
                asyncio.to_thread(ws.prepare, case, self.settings.workspaces, run["id"], attempt["id"])
            )
            try:
                workspace = await asyncio.shield(preparing)
            except asyncio.CancelledError:
                # A filesystem thread cannot be cancelled; own its result before cleanup.
                workspace = await preparing
                raise
            if cancel.is_set():
                attempt["status"] = "cancelled"
                return
            attempt.update(
                status="running", preparation_duration_ms=int((time.monotonic() - preparation) * 1000)
            )
            self.store.save_attempt(attempt, "attempt.status")
            parser = (
                adapters.AdapterParser(profile["adapter"], profile["model"])
                if profile["adapter"] != "mock"
                else None
            )

            async def on_line(line, stderr=False):
                nonlocal sequence
                if stderr:
                    return
                for event in parser.feed(line) if parser else []:
                    sequence += 1
                    event = sanitize(
                        {**event, "sequence": sequence, "timestamp": now(), "attempt_id": attempt["id"]},
                        secrets,
                    )
                    normalized.write(json.dumps(event) + "\n")
                    if event["type"] not in {"message.delta", "tool.updated"}:
                        self.store.event(
                            run["id"], "attempt.event_summary", {"attempt_id": attempt["id"], "event": event}
                        )

            if profile["adapter"] == "mock":
                start = time.monotonic()
                await asyncio.sleep(0.15)
                if profile["model"] == "reference":
                    proc = await asyncio.create_subprocess_exec(
                        "git",
                        "apply",
                        case["reference_patch"],
                        cwd=workspace,
                        env=environment,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    _, err = await proc.communicate()
                    if proc.returncode:
                        raise ValueError("Reference patch failed: " + err.decode(errors="replace"))
                (directory / "stdout.log").write_text("Test-only mock execution; no model called.\n")
                (directory / "stderr.log").write_text("")
                result = {
                    "exit_code": 0,
                    "agent_duration_ms": int((time.monotonic() - start) * 1000),
                    "final_response": "Applied reference solution."
                    if profile["model"] == "reference"
                    else "Left baseline unchanged.",
                    "tool_calls": 1,
                    "turns": 1,
                    "retries": 0,
                }
                attempt["command_preview"] = ["test-only", profile["model"]]
            else:
                command = adapters.build_command(profile, attempt["prompt"], workspace, attempt["id"])
                attempt["command_preview"] = sanitize(
                    [arg.replace(str(workspace), "<workspace>") for arg in command], secrets
                )
                self.store.save_attempt(attempt)
                process_result = await supervise(
                    command,
                    workspace,
                    environment,
                    attempt["effective_timeout_seconds"],
                    cancel,
                    directory / "stdout.log",
                    directory / "stderr.log",
                    on_line,
                    self.settings.execution["max_artifact_bytes"],
                    secrets,
                )
                result = {**parser.finalize(process_result["exit_code"]), **process_result}
                estimate_cost(result, profile.get("pricing"))
            attempt.update(sanitize(result, secrets))
            if process_result.get("secret_detected"):
                self.incident(run["id"], attempt["id"])
                attempt.update(
                    status="cancelled",
                    error_code="security_blocked",
                    error_message="Possible secret detected; run stopped.",
                )
                return
            capturing = asyncio.create_task(asyncio.to_thread(ws.capture, workspace))
            try:
                capture = await asyncio.shield(capturing)
            except asyncio.CancelledError:
                await capturing
                raise
            except ws.InvalidCandidateOutput as exc:
                status = (
                    "timed_out"
                    if process_result.get("timed_out")
                    else "adapter_error"
                    if attempt.get("error_code")
                    else "failed"
                )
                if cancel.is_set():
                    status = "cancelled"
                attempt.update(
                    status=status,
                    score=None if status == "cancelled" else 0.0,
                    full_pass=False,
                    error_code="timeout"
                    if status == "timed_out"
                    else attempt.get("error_code") or "invalid_candidate_output",
                    error_message=sanitize(str(exc), secrets),
                )
                attempt["graders"] = [
                    {
                        "id": "workspace_safety",
                        "component": "constraints",
                        "weight": 0.0,
                        "score": 0.0,
                        "passed": False,
                        "mandatory": True,
                        "summary": "Candidate cannot be graded safely: " + sanitize(str(exc), secrets),
                        "output": "",
                        "duration_ms": 0,
                        "infrastructure_error": False,
                    }
                ]
                return
            scrubber = StreamRedactor(secrets)
            diff = scrubber.feed(capture.pop("diff", "")) + scrubber.flush()
            if scrubber.detected:
                self.incident(run["id"], attempt["id"])
                attempt.update(
                    status="cancelled",
                    error_code="security_blocked",
                    error_message="Possible secret in patch; run stopped.",
                )
            patch_file = directory / "patch.diff"
            patch_writer = BoundedArtifact(patch_file, self.settings.execution["max_artifact_bytes"])
            patch_writer.write(diff)
            patch_writer.close()
            process_result["diff_truncated"] = patch_writer.truncated or capture.get("diff_truncated", False)
            attempt.update({k: v for k, v in capture.items() if k != "changed_paths"})
            if cancel.is_set():
                attempt["status"] = "cancelled"
                return
            if process_result.get("timed_out"):
                attempt.update(
                    status="timed_out",
                    score=0.0,
                    full_pass=False,
                    error_code="timeout",
                    error_message="Agent exceeded the configured timeout.",
                )
                return
            if attempt.get("error_code"):
                attempt.update(status="adapter_error", score=0.0, full_pass=False)
                return
            attempt["status"] = "grading"
            self.store.save_attempt(attempt, "attempt.status")
            start = time.monotonic()
            grading_task = asyncio.create_task(
                grade(case, workspace, {**capture, "diff": diff}, self.settings)
            )
            cancel_task = asyncio.create_task(cancel.wait())
            try:
                done, _ = await asyncio.wait([grading_task, cancel_task], return_when=asyncio.FIRST_COMPLETED)
                if cancel_task in done:
                    grading_task.cancel()
                    await asyncio.gather(grading_task, return_exceptions=True)
                    attempt["status"] = "cancelled"
                    return
                grading = await grading_task
            finally:
                cancel_task.cancel()
                if not grading_task.done():
                    grading_task.cancel()
                await asyncio.gather(cancel_task, grading_task, return_exceptions=True)
            grading = sanitize(grading, secrets)
            attempt.update(grading, grading_duration_ms=int((time.monotonic() - start) * 1000))
            if REDACTED in json.dumps(grading):
                self.incident(run["id"], attempt["id"])
                attempt.update(status="cancelled", error_code="security_blocked")
                return
            attempt["status"] = (
                "infrastructure_error"
                if grading.get("infrastructure_error")
                else "passed"
                if grading["full_pass"]
                else "failed"
            )
            (directory / "graders.json").write_text(json.dumps(attempt["graders"], indent=2))
        except asyncio.CancelledError:
            attempt.update(
                status="infrastructure_error",
                score=None,
                full_pass=None,
                error_code="interrupted",
                error_message="Runner stopped during attempt.",
            )
            raise
        except Exception as exc:
            attempt.update(
                status="infrastructure_error",
                score=None,
                full_pass=None,
                error_code="infrastructure",
                error_message=sanitize(str(exc), secrets),
            )
        finally:
            try:
                normalized.close()
                for kind, filename in [
                    ("events", "events.jsonl"),
                    ("stdout", "stdout.log"),
                    ("stderr", "stderr.log"),
                    ("diff", "patch.diff"),
                    ("graders", "graders.json"),
                ]:
                    path = directory / filename
                    if path.is_file():
                        os.chmod(path, 0o600)
                        text = path.read_text(errors="replace")
                        if workspace is not None:
                            text = text.replace(str(workspace), "<workspace>")
                        path.write_text(
                            sanitize(text.replace(str(self.settings.workspaces), "<workspaces>"), secrets)
                        )
                        attempt["artifacts"].append(
                            self.store.register_artifact(
                                attempt,
                                kind,
                                path,
                                normalized.truncated
                                if kind == "events"
                                else process_result.get(kind + "_truncated", False),
                            )
                        )
            finally:
                attempt["completed_at"] = now()
                if workspace is not None:
                    try:
                        if run["keep_workspaces"]:
                            attempt["workspace_retained"] = True
                        else:
                            try:
                                owned_delete(self.settings.workspaces, workspace)
                            except (OSError, ValueError):
                                attempt["workspace_retained"] = True
                                attempt["cleanup_warning"] = (
                                    "Workspace cleanup failed; manual inspection required."
                                )
                    finally:
                        ws.release(workspace)
            self.store.save_attempt(
                attempt, "attempt.completed" if attempt["status"] in {"passed", "failed"} else "attempt.error"
            )
            self.store.event(
                run["id"],
                "run.progress",
                {
                    "run_id": run["id"],
                    "completed": sum(a["status"] in TERMINAL for a in self.store.attempts(run["id"])),
                    "total": run["total_attempts"],
                },
            )

    def summary(self, run_id):
        run = self.store.run(run_id)
        attempts = self.store.attempts(run_id)
        run.pop("_suite", None)
        run["attempts"] = [
            {
                k: v
                for k, v in a.items()
                if k not in {"graders", "final_response", "command_preview", "prompt"}
            }
            for a in attempts
        ]
        run["completed_attempts"] = sum(a["status"] in TERMINAL for a in attempts)
        return sanitize({**run, **aggregate(run, attempts)})

    def router(self, run_id):
        return sanitize(router_summary(self.store.run(run_id), self.store.attempts(run_id)))

    async def cancel(self, run_id):
        run = self.store.run(run_id)
        if run_id in self.cancels and not self.tasks[run_id].done():
            self.cancels[run_id].set()
        return {"id": run_id, "status": run["status"]}

    async def rerun(self, attempt_id):
        async with self.creation_lock:
            return await self._rerun(attempt_id)

    async def _rerun(self, attempt_id):
        if self.fatal_error:
            raise ValueError("Storage failure: restart RouteBench after repairing local storage.")
        previous = self.store.attempt(attempt_id)
        run = self.store.run(previous["run_id"])
        if self.active() or run.get("security_blocked"):
            raise ValueError("Cannot rerun while active or blocked by security review")
        if previous["status"] not in {"infrastructure_error", "cancelled"}:
            raise ValueError("Only invalid or unstarted attempts can be rerun")
        if attempt_id not in {a["id"] for a in self.store.attempts(run["id"])}:
            raise ValueError("This attempt already has a replacement")
        suite = self.settings.load_suite(run["suite"]["id"], self.settings.suite_specs[run["suite"]["id"]])
        if suite["hash"] != run["suite"]["hash"]:
            raise ValueError("Suite changed; create a new run instead")
        await self.preflight()
        ident = previous["profile_id"]
        original_profile = next(p for p in run["profiles"] if p["id"] == ident)
        if ident not in self.settings.profiles or self.preflights[ident]["status"] != "ready":
            raise ValueError("Original profile is not ready")
        if {k: v for k, v in original_profile.items() if k != "preflight"} != self.settings.profiles[ident]:
            raise ValueError("Profile configuration changed; create a new run instead")
        job = self._attempt(
            run["id"], previous["case_id"], ident, previous["attempt_index"], previous["execution_order"]
        )
        job["supersedes_attempt_id"] = attempt_id
        run.update(status="queued", completed_at=None)
        self.store.save_run(run)
        self.store.save_attempt(job)
        self.start(run["id"], [job])
        return {"id": job["id"], "run_id": run["id"]}

    async def close(self):
        for cancel in self.cancels.values():
            cancel.set()
        if self.tasks:
            await asyncio.gather(*self.tasks.values(), return_exceptions=True)
        self.store.close()
