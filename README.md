# RouteBench

Compare coding-agent configurations with reproducible Python tasks and inspectable evidence. Runs stay local; your existing Pi, Claude Code, and Codex installations make their own provider requests using their existing authentication.

## Start

Prerequisites: Python 3.12+, uv, Node.js 22.12+, npm, and Git on macOS or Linux. Agent CLIs are optional for the offline demo.

```sh
uv sync --extra dev --python 3.12
cd frontend
npm ci
npm run build
cd ..
uv run routebench doctor
uv run routebench serve
```

Open **http://127.0.0.1:8765**. `doctor` only runs version/help checks; it never calls a model or reads credentials. Readiness establishes CLI compatibility, while authentication/model access are confirmed by an actual run.

This workspace's local `routebench.yaml` selects **http://127.0.0.1:8767** because port 8765 is already occupied. The example configuration keeps the default 8765.

For a demo that never calls a model:

```sh
ROUTEBENCH_MOCK=1 uv run routebench serve
```

The demo exposes two explicitly labeled test profiles: a reference solution and an unchanged baseline. It uses the real workspace, grading, persistence, API, and dashboard paths.

## Configure

Copy `routebench.example.yaml` to `routebench.yaml` to customize profiles, aliases, command argument arrays, timeouts, explicit dated prices, environment allowlists, and storage retention. Restart the server after editing. Alternatively pass `routebench --config /path/to/config.yaml serve`.

The current selectable profiles are:

- Pi: `Auto-3-API`.
- Claude Code (local YAML): `claude-opus-5`, `claude-sonnet-5`, `us.anthropic.claude-fable-5`, and `us.anthropic.claude-fable-5-1`.
- Codex: `gpt-6-sol`, `gpt-6-astra`, `gpt-5.6-sol`, `gpt-5.6-terra`, `gpt-5.6-luna`, and `gpt-5.5`.

The Codex IDs come from the installed CLI's model catalog. Rejected aliases (`sol`, `sol-5.6`, `terra`, and Claude `fable`) have been removed from the current configuration. Historical runs retain their original profiles and evidence. Existing CLI customizations are preserved. Every attempt starts a fresh session with an identical prompt and fixture. Model IDs are configurable and must be supported by your provider account.

“CLI ready” confirms executable/flag compatibility only. Model access is separately shown as verified by a completed run, failed, or unverified. A profile whose previous execution failed is left unselected and shows its error; you can explicitly select it to retry after correcting account access or configuration. GPT-6-Luna was removed from new-run selection at the user’s request; historical results remain available. GPT-5.6-Luna remains selectable.

The local YAML uses the user-supplied `us.anthropic.` Fable IDs; both passed the subsequent one-case check, along with `claude-sonnet-5`. Example Claude IDs remain configurable for other gateways. Historical failures with earlier aliases remain recorded.

Default execution is sequential, one attempt per case/profile, 900 seconds per attempt, with randomized profile order recorded using seed 42. Three repeats and parallel mode (maximum two) are available. Parallel timing includes machine contention.

The effective timeout is the shortest of the run, case, and profile caps and is saved on the attempt. One backend process owns a database at a time; use separate configuration/storage paths for an independent demo or test server.

Settings are maintained in YAML. The browser shows readiness and allows per-run settings, but never accepts credentials. If a CLI needs an environment variable, explicitly allow its name in that profile's `env_allowlist`; values are never saved in snapshots or exports. Repository commands can access inherited values.

## Suites and evidence

- **Smoke:** RB-PY-001, RB-PY-003, RB-PY-005; three executions per selected profile (33 with all eleven).
- **Core:** nine cases spanning bug fixes, features, multi-file changes, refactoring, path safety, and a planning/execution workflow; nine executions per selected profile (99 with all eleven).
- **Planning:** RB-PY-009 only, also included in Core; one execution per selected profile (11 with all eleven). The agent writes a plan, implements inventory reservations, adds tests, and records verification in one session.

Each attempt receives a fresh Git repository. After execution, RouteBench captures a canonical diff (including new files), grades a separate disposable copy against hidden tests, and stores results under `.routebench/`.

The Planning task's `PLAN.md` and `VERIFICATION.md` appear in the saved diff. Checks validate required sections and implementation behavior; they do not judge prose quality, prove planning happened first, or require model switching. Existing route observations show what the CLI reported during the session, without inferred stage boundaries. See the [task contract](evals/python-core/fixtures/RB-PY-009/README.md).

Correctness uses functional groups (80%), regression tests (10%), and deterministic constraints (10%). Mandatory checks must pass for a full pass. Infrastructure problems have null scores and explicit reruns; they do not masquerade as zero-quality model output. Timeout and unrecoverable agent failures remain failures. The transport-refactoring baseline fails its structural requirement while preserving its existing behavior.

The backend owns all rankings, repeat statistics, and Pareto frontiers. Missing cost/route/token data stays missing. CLI-reported cost and manually estimated cost have distinct provenance; neither claims to be an invoice. Incomplete comparisons suppress definitive winner labels. Route observations across different CLI harnesses are observational, not causal.

Codex cost is **estimated** from uncached input, cached input, and output tokens at the saved Pi catalog list prices (snapshot: 2026-09-23). The six Codex profiles include explicit dated rates. Each estimate saves its rates and calculation breakdown; UI info controls explain the source and formula. Incomplete usage or missing prices stays unavailable. These estimates are not actual ChatGPT subscription or credit charges. See [the calculation and offline historical backfill workflow](docs/working.md#codex-cost-estimates).

The attempt inspector exposes graders, patch statistics and diff, normalized events, final response, and metadata. JSON export includes historical attempts, evidence references, and provenance. Separate CSV exports cover leaderboard and attempts. Hidden test source, full environments, and credentials are excluded.

## Recovery and retention

Closing a browser does not stop a run. Cancellation stops scheduling and terminates supervised process groups. After an interrupted backend, completed results survive, active work becomes invalid, and unstarted work becomes cancelled. Use explicit rerun actions; no paid work resumes automatically.

Successful evidence is persisted before workspace cleanup. Set `keep_workspaces` to retain workspaces. Artifacts expire after 30 days by default, checked at startup; summaries remain until explicit deletion. The History screen can delete terminal runs and their owned artifacts.

## Verify

```sh
uv run routebench validate-suite
uv run pytest
uv run ruff check backend tests
cd frontend
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

Automated checks use mock processes, synthetic streams, and minimized actual CLI recordings. The browser suite starts its own temporary mock backend and never calls a model. Real validation can incur provider charges. Development validation used all 24 authorized launch slots, including four failed wrapper starts, two checks of replacement IDs, and six checks of correctly named Codex models. Results and access failures are recorded in [the validation report](docs/VALIDATION_REPORT.md).

For frontend development, run the backend on port 8765 and `npm run dev` in `frontend/`; Vite proxies API requests.

## Local security boundary

V1 supports only trusted, locally authored fixtures. A temporary repository copy is **not a host security sandbox**. Pi and CLI plugins/hooks can access resources available to your OS user. Codex uses workspace-write mode; RouteBench never enables permission bypass flags or reads credential files.

The server binds to loopback, rejects cross-origin mutations, escapes output, bounds logs, validates owned paths, and redacts likely secrets before persistence. A possible secret stops the run and blocks exports. These checks reduce accidental disclosure; they are not a substitute for OS/container isolation.

Full product and technical specifications are in [docs/README.md](docs/README.md).
