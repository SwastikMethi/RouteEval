import asyncio
import csv
import io
import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .config import ROOT, Settings
from .runner import RUN_TERMINAL, Runner
from .security import owned_delete, safe_child, sanitize


class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suite_id: str = Field(min_length=1, max_length=80)
    profile_ids: list[str] = Field(min_length=1, max_length=30)
    attempts_per_case: Literal[1, 3] = 1
    mode: Literal["sequential", "parallel"] = "sequential"
    timeout_seconds: int = Field(default=900, ge=1, le=7200)
    random_seed: int = Field(default=42, ge=0, le=2**32 - 1)
    keep_workspaces: bool = False


def create_app(settings=None):
    settings = settings or Settings()
    runner = Runner(settings)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await runner.close()

    app = FastAPI(title="RouteBench", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.runner = runner

    @app.middleware("http")
    async def local_boundary(request, call_next):
        host = urlsplit("http://" + request.headers.get("host", "")).hostname
        if host not in {"127.0.0.1", "localhost", "::1", "testserver"}:
            return JSONResponse({"detail": "Only local requests are accepted"}, status_code=403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            expected = f"{request.url.scheme}://{request.headers.get('host')}"
            if request.headers.get("x-routebench-request") != "1" or (origin and origin != expected):
                return JSONResponse({"detail": "Cross-origin mutation rejected"}, status_code=403)
            if int(request.headers.get("content-length", "0")) > 1024 * 1024:
                return JSONResponse({"detail": "Request exceeds 1 MiB"}, status_code=413)
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 1024 * 1024:
                    return JSONResponse({"detail": "Request exceeds 1 MiB"}, status_code=413)
            request._body = bytes(body)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store" if request.url.path.startswith("/api/") else "no-cache"
        return response

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        return JSONResponse({"detail": "Resource not found"}, status_code=404)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": sanitize(str(exc))}, status_code=400)

    @app.get("/api/v1/system")
    async def system():
        return {
            "version": __version__,
            "local_only": True,
            "mock_enabled": any(p["adapter"] == "mock" for p in settings.profiles.values()),
            "defaults": {
                "timeout_seconds": settings.execution["timeout_seconds"],
                "attempts_per_case": settings.execution["attempts"],
                "mode": settings.execution["mode"],
            },
            "storage": sanitize(settings.storage),
            "warnings": [runner.fatal_error] if runner.fatal_error else [],
        }

    @app.get("/api/v1/profiles")
    async def profiles():
        if not runner.preflights:
            await runner.preflight()
        return {"profiles": runner.profiles()}

    @app.post("/api/v1/profiles/preflight")
    async def preflight():
        return {"profiles": await runner.preflight()}

    @app.get("/api/v1/suites")
    async def suites():
        return {"suites": [settings.public_suite(s) for s in settings.suites.values()]}

    @app.get("/api/v1/suites/{suite_id}")
    async def suite(suite_id: str):
        return settings.public_suite(settings.suites[suite_id])

    @app.post("/api/v1/runs", status_code=201)
    async def create(request: RunRequest):
        return await runner.create(request.model_dump())

    @app.get("/api/v1/runs")
    async def history():
        return {"runs": [runner.summary(r["id"]) for r in runner.store.runs()]}

    @app.get("/api/v1/runs/{run_id}")
    async def summary(run_id: str):
        return runner.summary(run_id)

    @app.post("/api/v1/runs/{run_id}/cancel")
    async def cancel(run_id: str):
        return await runner.cancel(run_id)

    @app.delete("/api/v1/runs/{run_id}")
    async def delete(run_id: str):
        run = runner.store.run(run_id)
        if run["status"] not in RUN_TERMINAL:
            raise HTTPException(409, "Cancel and finish the run before deleting it")
        for attempt in runner.store.attempts(run_id, effective=False):
            directory = safe_child(settings.workspaces, f"{run_id}--{attempt['id']}")
            if directory.exists():
                owned_delete(settings.workspaces, directory)
        directory = safe_child(settings.artifacts, run_id)
        if directory.exists():
            owned_delete(settings.artifacts, directory)
        runner.store.delete(run_id)
        return {"deleted": True}

    @app.get("/api/v1/runs/{run_id}/events")
    async def events(run_id: str, request: Request):
        runner.store.run(run_id)
        try:
            cursor = max(0, int(request.headers.get("last-event-id", request.query_params.get("after", "0"))))
        except ValueError:
            raise HTTPException(400, "Invalid event cursor")

        async def stream():
            nonlocal cursor
            idle = 0
            while not await request.is_disconnected():
                rows = runner.store.events(run_id, cursor)
                for event in rows:
                    cursor = event["sequence"]
                    yield f"id: {cursor}\nevent: {event['type']}\ndata: {json.dumps(event['data'])}\n\n"
                if runner.store.run(run_id)["status"] in RUN_TERMINAL and not rows:
                    break
                if idle >= 30:
                    yield ": heartbeat\n\n"
                    idle = 0
                idle += 1
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.get("/api/v1/runs/{run_id}/matrix")
    async def matrix(run_id: str):
        run = runner.summary(run_id)
        return {"attempts": run["attempts"], "cases": run["suite"]["cases"], "profiles": run["profiles"]}

    @app.get("/api/v1/runs/{run_id}/router")
    async def router(run_id: str):
        return runner.router(run_id)

    @app.get("/api/v1/attempts/{attempt_id}")
    async def attempt_detail(attempt_id: str):
        attempt = runner.store.attempt(attempt_id)
        run = runner.store.run(attempt["run_id"])
        return sanitize(
            {
                **attempt,
                "case": next(c for c in run["suite"]["cases"] if c["id"] == attempt["case_id"]),
                "profile": next(p for p in run["profiles"] if p["id"] == attempt["profile_id"]),
            }
        )

    def artifact_path(attempt_id, kind):
        attempt = runner.store.attempt(attempt_id)
        registered = next((a for a in attempt["artifacts"] if a["kind"] == kind), None)
        if registered:
            return runner.store.artifact(registered["id"])[1]
        if attempt["status"] not in {"running", "grading"}:
            return None
        # A live attempt's owned stream is readable before final hash registration.
        return safe_child(
            settings.artifacts,
            Path(attempt["run_id"]) / attempt_id / ("events.jsonl" if kind == "events" else "patch.diff"),
        )

    @app.get("/api/v1/attempts/{attempt_id}/events")
    async def attempt_events(
        attempt_id: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500)
    ):
        path = artifact_path(attempt_id, "events")
        data = []
        total = 0
        if path and path.is_file():
            with path.open(errors="replace") as source:
                for line in source:
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if offset <= total < offset + limit:
                        data.append(event)
                    total += 1
        return {"events": data, "total": total}

    @app.get("/api/v1/attempts/{attempt_id}/diff", response_class=PlainTextResponse)
    async def diff(attempt_id: str):
        path = artifact_path(attempt_id, "diff")
        return (
            sanitize(path.read_text(errors="replace").replace(str(settings.workspaces), "<workspaces>"))
            if path and path.is_file()
            else "Patch unavailable (attempt incomplete or artifact retention elapsed)."
        )

    @app.get("/api/v1/attempts/{attempt_id}/graders")
    async def graders(attempt_id: str):
        return {"graders": runner.store.attempt(attempt_id)["graders"]}

    @app.post("/api/v1/attempts/{attempt_id}/rerun", status_code=201)
    async def rerun(attempt_id: str):
        return await runner.rerun(attempt_id)

    @app.get("/api/v1/artifacts/{artifact_id}")
    async def artifact(artifact_id: str):
        metadata, path = runner.store.artifact(artifact_id)
        if not path.is_file():
            raise HTTPException(410, "Artifact retention elapsed")
        # Legacy artifacts may predate path scrubbing. Never expose host paths through the UI.
        content = sanitize(path.read_text(errors="replace").replace(str(settings.workspaces), "<workspaces>"))
        return PlainTextResponse(
            content, headers={"Content-Disposition": f'attachment; filename="{metadata["kind"]}.txt"'}
        )

    def exportable(run_id):
        if runner.store.run(run_id).get("security_blocked"):
            raise HTTPException(409, "Exports blocked after a possible secret was detected")
        return runner.summary(run_id)

    @app.get("/api/v1/runs/{run_id}/export.json")
    async def export_json(run_id: str):
        run = exportable(run_id)
        return JSONResponse(
            {
                "schema_version": 1,
                **run,
                "attempt_history": sanitize(runner.store.attempts(run_id, effective=False)),
            },
            headers={"Content-Disposition": f'attachment; filename="{run_id}.json"'},
        )

    @app.get("/api/v1/runs/{run_id}/export.csv")
    async def export_csv(run_id: str, table: Literal["leaderboard", "attempts"] = "leaderboard"):
        run = exportable(run_id)
        rows = run[table]
        columns = (
            [
                "profile_id",
                "label",
                "mean_score",
                "full_passes",
                "total_attempts",
                "pass_rate",
                "completion_rate",
                "median_agent_duration_ms",
                "total_cost_usd",
                "median_cost_usd",
                "cost_coverage",
                "cost_provenance",
                "cost_sources",
            ]
            if table == "leaderboard"
            else [
                "case_id",
                "profile_id",
                "attempt_index",
                "status",
                "score",
                "full_pass",
                "agent_duration_ms",
                "preparation_duration_ms",
                "grading_duration_ms",
                "input_tokens",
                "output_tokens",
                "cached_input_tokens",
                "cache_write_tokens",
                "cost_usd",
                "cost_provenance",
                "cost_estimate",
                "primary_observed_model",
                "files_changed",
                "lines_added",
                "lines_removed",
                "error_code",
            ]
        )
        target = io.StringIO()
        writer = csv.DictWriter(target, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # Spreadsheet programs must not execute agent-controlled formulas.
            writer.writerow(
                {
                    key: "'" + value
                    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r"))
                    else json.dumps(value) if isinstance(value, (dict, list)) else value
                    for key, value in row.items()
                }
            )
        return PlainTextResponse(
            target.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{table}.csv"'},
        )

    @app.get("/api/v1/documentation", response_class=PlainTextResponse)
    async def documentation():
        return (ROOT / "README.md").read_text()

    dist = ROOT / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    else:

        @app.get("/", response_class=PlainTextResponse)
        async def build_hint():
            return "RouteBench API is running. Build the dashboard: cd frontend && npm ci && npm run build"

    return app
