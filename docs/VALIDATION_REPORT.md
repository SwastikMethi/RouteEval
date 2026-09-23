# RouteBench validation — updated 2026-09-24

The local MVP is implemented: CLI execution, hidden deterministic grading, local persistence and recovery, live dashboard, result comparison, route inspection, history, and exports. The initial validation below used the six documented aliases. Subsequent user-requested updates use `claude-opus-5` and the exact Codex catalog IDs listed in the [setup guide](../README.md#configure). Historical run snapshots remain unchanged.

## Automated verification

| Check | Result |
| --- | --- |
| Backend suite | Previous full suite: 212 tests and 38 subtests passed, plus the targeted future-attempt cost test. Latest Planning change: all 106 eval, runner/API, and configuration tests passed |
| Python lint | Ruff passed |
| Nine-case suite validation | Every reference passed; every baseline failed its intended requirement; representative incorrect mutations were rejected. Missing Planning documents also prevented full pass |
| Full offline core run (release 1.0.0) | 16/16 attempts completed: eight reference passes, eight expected baseline failures |
| Restart and evidence | All 16 core results, summaries, events, and 80 artifact hashes survived Runner/Store close and reopen |
| Workspace cleanup | Empty after the core run; owned abandoned grader overlays removed at startup |
| Frontend component tests | 13 passed, including cost provenance and tooltip interactions |
| Browser tests | Previous six-test suite passed. Latest Planning change: two integration checks passed, covering suite selection and a six-attempt mock run with evidence/export/history/mobile checks |
| Production build | TypeScript and Vite passed |
| Frontend dependency audit | No reported vulnerabilities |

Failure checks cover cancellation, timeout, backend death, descendant termination, grading isolation, invalid candidate output, storage failures, redaction, artifact fingerprints, recovery without automatic execution, SSE replay, request origin checks, missing metrics, and rerun lineage. Browser checks use a temporary mock backend and do not call models.

Non-blocking diagnostics: Starlette reports a TestClient/httpx deprecation; Vite reports a large JavaScript chunk from React/Recharts. The application build and tests succeed. Current runtime target is macOS/Linux, Python 3.12+, and Node 22.12+.

## Actual installed CLI checks

Version/help checks observed Pi **0.84.4**, Claude Code **2.1.280**, and Codex **0.156.1**. They support the required command flags. Version/help readiness does not establish model access; actual execution produced these results on RB-PY-001:

| Profile | Result | Observed detail |
| --- | --- | --- |
| Pi `Auto-3-API` | Passed | Response model `gpt-5.6-sol` reported in completed-message metadata |
| Claude `sonnet` | Passed | Observed `claude-sonnet-5` |
| Claude `opus` | Provider error | Gateway rejected resolved ID `claude-opus-5-5` with HTTP 400 |
| Claude `fable` | Provider error | Gateway rejected resolved ID `claude-fable-5-1` with HTTP 400 |
| Codex `sol` | Provider error | Model not supported when using Codex with this ChatGPT account |
| Codex `terra` | Provider error | Model not supported when using Codex with this ChatGPT account |

Run: `run_1696dd8b0c3940a3`. All failures remain recorded. As explicitly requested, aliases were retained rather than replaced. Successful Codex execution has therefore not been validated in this environment.

## Actual smoke run

Only the two profiles with successful access were run across the three smoke cases. The per-attempt limit for this validation was **300 seconds**.

| Profile | RB-PY-001 | RB-PY-003 | RB-PY-005 | Full passes | Mean score |
| --- | --- | --- | --- | --- | --- |
| Pi `Auto-3-API` | Passed | Passed | Passed | 3/3 | 1.000 |
| Claude `sonnet` | Passed | Passed | Timed out | 2/3 | 0.667 |

Run: `run_4a83a21aebbf4e64`. This is one attempt per case, not a reliability estimate. Concurrent implementation tests/builds make recorded timing diagnostic rather than a controlled performance comparison.

Pi reported zero cost through the gateway; this is retained as **reported**, not proof of free provider usage. Sonnet's timed-out attempt has unavailable final cost. Missing route, usage, and cost values remain missing. Pi's final smoke case did not expose an observed route; no route was inferred. CLI route metadata was captured, but it was not independently correlated against an interactive Pi TUI session.

## Launch accounting and evidence corrections

The authorized limit was 24 validation launches. **16 slots were used conservatively:** six initial profile checks, six smoke launches, and four replacement launches. Four of the smoke wrapper starts failed before launching a CLI because a supervisor-interface update occurred during validation. They remain in history as infrastructure errors with null scores and an explanatory warning; explicit linked replacements provide the effective results above. Thus twelve starts reached the actual CLI, and all sixteen count against the cap. No additional real model calls were made for final testing.

Saved streams were re-normalized after parser fixes to remove synthetic error-model routes and recover reported reasoning-token counts. Reasoning content is excluded from normalized display events. Stored artifact paths were subsequently scrubbed and fingerprints refreshed. These steps used retained evidence without rerunning agents; run warnings record both transformations. Minimized actual parser fixtures and capture provenance are in `tests/fixtures/`.

Real evidence is retained locally under `.routebench/`, excluded from Git, and available through History and JSON/CSV export. Summaries persist beyond the default 30-day artifact retention period.

## Validation limits

### Model-ID follow-up

The user supplied replacement IDs after inspecting the original errors. Local and example YAML now use `claude-opus-5` and `sol-5.6`; Fable and Terra are unchanged. The user also requested cancellation of core run `run_486370aacde5495b`: completed results were preserved, and active/unstarted work was cancelled before restart. That separately user-initiated run is outside the development-validation launch ledger.

Two additional validation launches were recorded in `run_c7e39eaf21434c24`, bringing the development ledger to **18/24**:

| Replacement model | RB-PY-001 outcome |
| --- | --- |
| Claude `claude-opus-5` | Accepted; full pass, score 1.0 |
| Codex `sol-5.6` | Still rejected: model not supported when using Codex with this ChatGPT account |

Claude completed successfully after a denied optional plugin tool call. This exposed and fixed an adapter bug that treated every permission denial as fatal. Successful terminal results now retain such denials as warnings and proceed to grading; error terminal results remain failures. The saved Opus patch was reconstructed with identical baseline/post tree hashes and graded offline. The original classification and correction are retained in evidence; no replacement model call was needed.

Profile cards now distinguish CLI readiness from model access, display the previous failure, and leave previously failed profiles unselected until an explicit retry. Follow-up checks passed: 91 configuration/adapter tests with 31 subtests before the parser fix; 38 adapter/runner regression tests with 31 subtests after it; nine UI tests, TypeScript/Vite build, and Ruff.

### Codex catalog follow-up

The installed Codex CLI's `model/list` response was queried through its local app-server protocol, using existing login and no model turn. This returned seven visible models, including GPT-6-Luna in addition to the six in the supplied screenshot. The protocol is described in the [official Codex app-server documentation](https://learn.chatgpt.com/docs/app-server). Exact catalog IDs replaced guessed aliases in both local and example YAML. Claude Fable was removed from current setup; past snapshots remain intact.

| Configured Codex model | Validation outcome |
| --- | --- |
| `gpt-6-sol` | Full pass, score 1.0 |
| `gpt-6-astra` | Full pass, score 1.0 |
| `gpt-6-luna` | Advertised by CLI; no model execution attempted |
| `gpt-5.6-sol` | Model execution started; cancelled by a false-positive redaction guard before grading |
| `gpt-5.6-terra` | Full pass, score 1.0 |
| `gpt-5.6-luna` | Launched, then cancelled with the interrupted batch; no completed validation |
| `gpt-5.5` | Full pass, score 1.0 |

Runs: `run_a922803176324e52` and `run_39be52afa2db4906`. Six additional CLI launches bring the ledger to **24/24**. Checks used RB-PY-001, a 300-second cap, and two concurrent processes. Timing includes local test/build contention and is not a controlled benchmark. No request with a corrected catalog ID returned the previous unsupported-model error, but the incomplete/unattempted rows above are not claimed as completed validations.

The redaction guard misidentified `sk-` inside a `flask-…` project identifier in a tool response. The shared token pattern now requires a token boundary. Regression checks preserve real-token redaction at every chunk boundary while accepting ordinary project names. The interrupted run retains its original cancellation/export block and an explanatory review warning. No interrupted score was fabricated or replayed as a completed agent execution.

Ten current profiles are selectable: these seven Codex models, Pi AutoRouter, Claude Opus 5, and Claude Sonnet. The UI separately marks completed-run verification and unverified access. Current checks: 192 backend tests plus 38 subtests, ten UI tests, production build, and Ruff passed.

### Fable profiles and Codex cost follow-up

At the user's request, both local and example configurations now include `claude-fable-5` and `claude-fable-5-1`, bringing the selectable total to twelve. The installed Claude CLI help uses `claude-fable-5` as a full-name example. Both new profiles remain unverified; the earlier `fable` alias resolved to `claude-fable-5-1` and was rejected by the gateway. No additional model launches were made; the validation ledger remains 24/24.

Recorded Codex `turn.completed` events contain token usage but no USD cost. No estimation prices are configured, so `cost_usd` stays null with `unavailable` provenance. This preserves the distinction between observed usage, optional API-rate estimates, and actual ChatGPT plan billing.

### User-edited YAML rerun

The user changed the local YAML and explicitly requested another run. Run `run_392303b3a2af4953` checked the three changed Claude profiles on RB-PY-001, one attempt each, sequentially with a 300-second limit. All three completed and passed every grading requirement:

| Exact configured model | Score | CLI-reported cost (USD) |
| --- | --- | --- |
| `us.anthropic.claude-fable-5` | 1.0 | 0.3930713 |
| `claude-sonnet-5` | 1.0 | 0.2638481 |
| `us.anthropic.claude-fable-5-1` | 1.0 | 1.4396115 |

These three user-requested launches are recorded separately from the original 24-slot development ledger. The gateway accepted both prefixed Fable IDs. Costs are reported by the CLI, not independently verified billing. These are single-case access/correctness checks, not full-suite or reliability results. The user's YAML edits were preserved.

### Codex estimate implementation — 2026-09-24

Six remaining Codex profiles now have dated Pi catalog list prices. GPT-6-Luna was removed from local and example YAML, while GPT-5.6-Luna and historical results are retained. The calculation and exact rates are documented in [working.md](working.md#codex-cost-estimates).

The supplied example was reproduced exactly: `$0.1156772`, displayed as `$0.1157`. Checks cover distinct turns, duplicate terminal events, cached-input subtraction, reasoning-token exclusion, missing/invalid usage, omitted rates, zero cost, and cache-write token parsing. Estimates save their pricing snapshot and token-bucket contributions. Aggregate results and JSON/CSV exports retain estimated/reported/mixed provenance.

Backfill tests verify artifact fingerprints, preservation of original records, database backup contents, and repeatability. A disposable copy of actual historical data produced five estimates without changing run snapshots, scores, statuses, or artifacts. Browser checks verified the label, saved rates, inspector breakdown, keyboard dismissal, mobile tap, and viewport containment. The final hover/readability adjustment also passed its targeted browser check. A separate simulated-stream test also verified the full future-attempt path through persistence. No additional model executions were launched for this implementation.

The initial full backend check hit the filesystem sandbox's restriction on `ps` in ten process-cleanup tests; rerunning with process inspection permitted passed all 212 tests and 38 subtests. Ruff, component tests, and the production build also passed. The existing Starlette deprecation and large frontend bundle notices remain non-blocking.

The live rollout waited until no runs remained active. Run `run_9dc287957737497a` was marked cancelled with 23 passes, two adapter errors, and 11 cancelled attempts. The offline backfill then estimated 18 historical Codex attempts and created `.routebench/routebench.before-costs-22e599333c36.db` before writing. A comparison with that backup confirmed run snapshots, artifact records, scores, and statuses were unchanged; only cost metadata and recovered usage completeness/cache-write fields changed. Repeating the preview found zero eligible records. RouteBench restarted on port 8767 and returned all 11 configured profiles, including GPT-5.6-Luna and both user-configured Fable IDs, with GPT-6-Luna absent. Read-only checks against the restarted API confirmed that summary, attempt detail, JSON export, and both CSV tables agree on estimates and provenance for `run_39be52afa2db4906`: GPT-6-Sol `$0.116512` and GPT-5.5 `$0.3628702`. No model executions were started by the rollout.

### Planning and execution task — 2026-09-24

Added RB-PY-009, “Inventory reservations: plan, implement, verify,” to Core release 1.1.0 and the separately selectable `python-planning` subset. One definition supplies both views; Smoke still contains its original three cases. The task requires plan and verification artifacts, atomic reservation behavior, idempotent retries/cancellation, immutable records, and added visible tests. Its 80/10/10 score split matches the existing suite. Document checks establish completeness rather than prose quality, stage ordering, or the truth of self-reported results; model switching is not required or scored.

Validation passed all 106 tests in `test_eval_suite.py`, `test_runner_api.py`, and `test_security_config.py`. The new case passed baseline/reference/mutation checks: the reference fully passes, unfinished behavior fails, and double-restoration on cancellation is detected. Removing either workflow document prevents full pass while functional behavior still passes. The reference's four visible tests also passed directly. Ruff passed for backend/tests, the browser backend helper, and the new fixture/graders; documentation links resolved.

The new mock API run completed two attempts, with one reference pass and one expected baseline failure. It verified the saved plan, verification report, and added tests in the diff, grader results, JSON evidence references, and CSV export. Two Playwright integration checks passed: Planning exposes one task, Core nine, Smoke three, and the existing mock run still grades, persists, and exports correctly. No live model executions were launched.

After confirming no active runs, the local server restarted on port 8767. Read-only API checks verified all three suite selections at version 1.1.0 and identical RB-PY-009 metadata between Planning and Core. All ten previously saved run records remained byte-for-byte unchanged. The production frontend required no code or build changes because it already renders suite definitions dynamically.

### Remaining limits

A full real 48-execution core run and a three-repeat real reliability run were not performed. The full core pipeline was verified offline. Rejected model IDs still require provider/account support before successful real comparisons are possible; their failures remain visible in profile warnings and attempt evidence.

Fixtures are trusted local repositories. Temporary copies are not an OS sandbox, and existing CLI customizations/authentication are reused. Settings remain YAML-controlled; restarting the backend does not automatically resume paid work.
