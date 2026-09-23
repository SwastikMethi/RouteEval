# Implementation Plan

## 1. Strategy

Build one thin end-to-end path first:

> One Python case → one mock profile → one workspace → one grader → one stored result → one visible UI cell

Then add real adapters and comparison views. This keeps the prototype testable and prevents the frontend, adapters, and eval set from being developed as disconnected scaffolding.

## 2. Phase 0 — Installed-CLI validation spike

### Deliverables

- Record installed versions of Pi, Claude Code, and Codex.
- Verify current non-interactive flags through `--help`.
- Run one harmless temporary-repository task per CLI after user initiation.
- Save sanitized sample structured output.
- Confirm Pi route fields match the model shown in its TUI.
- Confirm usage/cost availability for all private aliases.

### Exit criteria

- A versioned parser fixture exists for every installed CLI.
- Any command overrides needed by the user's environment are documented.
- Router and cost capabilities are marked supported, unsupported, or unknown.

## 3. Phase 1 — Domain model and configuration

### Deliverables

- Python project skeleton
- Pydantic schemas for profiles, suites, cases, graders, runs, attempts, usage, cost, and routes
- YAML configuration loader
- Suite loader and validation
- Redaction and configuration fingerprinting
- SQLite migration framework

### Tests

- Valid and invalid profile examples
- Private model aliases
- Duplicate IDs
- Relative path resolution
- Secret-value exclusion
- Suite hash stability

### Exit criteria

- `routebench doctor` can load config and show suite/profile validation without running an agent.

## 4. Phase 2 — Workspace and grading vertical slice

### Deliverables

- Trusted fixture copy
- Symlink rejection
- Git baseline initialization
- Safe diff capture
- Hidden grader overlay
- Command and file-constraint graders
- Weighted score and full-pass logic
- Mock adapter

### Tests

- Workspace path cannot escape root
- Baseline hash is stable
- Hidden tests are absent during mock-agent execution
- Reference solution passes
- Test edits are rejected
- Cleanup targets only the owned attempt path

### Exit criteria

- A mock profile can complete one case and produce a deterministic score, evidence, and diff.

## 5. Phase 3 — Run orchestration and persistence

### Deliverables

- Run expansion into attempts
- Seeded randomized order
- Sequential scheduler
- Attempt state machine
- Process supervisor with timeout/cancellation
- Incremental event/artifact persistence
- Run recovery after API restart

### Tests

- One failed attempt does not stop later attempts
- Timeout kills child processes
- Cancellation preserves completed results
- Database failure stops new scheduling safely
- Event sequence remains monotonic

### Exit criteria

- A complete mock smoke run survives restart and can be reopened.

## 6. Phase 4 — Real CLI adapters

Recommended order:

1. Pi
2. Codex
3. Claude Code

### Pi deliverables

- JSONL parser
- Tool and retry events
- Usage and cost aggregation
- Provider/model/response-model route extraction
- Multi-route attempt support

### Codex deliverables

- `codex exec` invocation
- JSONL item parser
- Workspace-write sandbox mode
- Token aggregation
- Git repository validation

### Claude Code deliverables

- Headless invocation
- Stream JSON parser
- Permission-mode compatibility
- Usage, model usage, and total-cost parsing

### Tests

- Sanitized parser contract fixtures
- Malformed/unknown event tolerance
- No cumulative usage double counting
- Missing optional fields
- Adapter and authentication errors

### Exit criteria

- Each installed CLI can finish one smoke case and create a normalized attempt result.

## 7. Phase 5 — API and live events

### Deliverables

- FastAPI routes from the data specification
- SSE stream with cursor replay
- Run cancellation
- Attempt detail and artifacts
- JSON and CSV exports

### Tests

- Request validation
- SSE reconnection
- Cross-origin mutation protection
- Artifact path authorization
- Export redaction

### Exit criteria

- A browser client can start, follow, reopen, and export a run using only the API.

## 8. Phase 6 — UI vertical slice

### Step 1: run setup

- Suite selector
- Profile cards and readiness
- Invocation calculation
- Timeout and attempts
- Start action

### Step 2: live run

- Progress rail
- Case matrix
- Live attempt status
- Running-attempt side panel

### Step 3: result overview

- KPI cards
- Leaderboard
- Heatmap
- Category bars
- Quality/time scatter
- Quality/cost scatter
- Pareto frontier

### Step 4: details

- Evidence-first attempt drawer
- Diff
- Timeline
- Output and metadata

### Step 5: router and history

- Route distribution and outcome views
- Run history and compatible-run comparison

### Tests

- Component states with fixture data
- Empty/missing metric states
- Responsive layouts
- Keyboard navigation
- Reduced-motion mode
- Playwright end-to-end mock run

### Exit criteria

- The full mock smoke run can be controlled and explained without opening a terminal.

## 9. Phase 7 — Build the Python eval suite

Implement cases in increasing complexity:

1. RB-PY-001
2. RB-PY-003
3. RB-PY-005
4. RB-PY-008
5. RB-PY-002
6. RB-PY-004
7. RB-PY-006
8. RB-PY-007

The first three form the smoke suite.

For every case:

1. Write fixture and public documentation.
2. Verify baseline failure.
3. Write hidden behavior groups.
4. Write and review reference solution.
5. Verify full pass.
6. Add incorrect mutations and confirm grader sensitivity.
7. Freeze metadata and hashes.

### Exit criteria

- All eight cases pass the release checklist in `EVAL_SET_SPEC.md`.

## 10. Phase 8 — Hardening and final validation

### Deliverables

- Security preflight
- Secret-pattern redaction
- Log/artifact limits
- Failure recovery
- Cost-provenance labeling
- Cross-harness explanation in UI and exports
- Documentation and setup guide

### End-to-end validation order

1. Mock smoke run
2. One real profile on smoke suite
3. All ready profiles on one case
4. All six profiles on smoke suite
5. Full eight-case core run
6. Optional three-attempt smoke reliability run

## 11. Test matrix

| Area | Unit | Integration | End-to-end |
| --- | --- | --- | --- |
| Config and suites | Yes | Yes | Doctor command |
| Workspaces | Yes | Yes | One real attempt |
| Graders | Yes | Yes | Reference solutions |
| Adapters | Parser fixtures | Real preflight | User-initiated smoke |
| Metrics | Yes | Stored run | Dashboard values |
| API/SSE | Yes | Yes | Browser run |
| UI | Component | API fixtures | Playwright |
| Security | Redaction/path tests | Malicious fixture simulations | Localhost and export review |

## 12. MVP definition of done

Implemented and checked on 2026-09-23. See [validation results](VALIDATION_REPORT.md) for actual CLI access failures, launch accounting, and the distinction between offline core validation and real smoke validation.

- [x] Six configured profiles appear with accurate readiness.
- [x] Smoke and core suite manifests validate.
- [x] One fresh workspace is used per attempt.
- [x] Hidden graders remain unavailable during agent execution.
- [x] Pi, Claude Code, and Codex adapters parse current installed outputs.
- [x] Pi route model is captured when present.
- [x] Correctness, reliability, timing, usage, cost provenance, tools, and patch metrics are stored.
- [x] Live matrix accurately reflects attempt states.
- [x] Leaderboard follows quality-first ranking.
- [x] Time and cost Pareto views handle missing values.
- [x] Attempt inspector explains every score.
- [x] One adapter failure does not abort the run.
- [x] Exports exclude credentials and hidden-test source.
- [x] A complete core run can be reopened after application restart.
- [x] Security limitations are visible before real execution.

## 13. Explicitly deferred work

- Containers and strong host isolation
- Public benchmark imports
- Non-Python languages
- Remote workers and CI
- Router training
- LLM judge
- Hosted accounts and collaboration
- Public sharing and leaderboards
- Automated statistical significance claims
