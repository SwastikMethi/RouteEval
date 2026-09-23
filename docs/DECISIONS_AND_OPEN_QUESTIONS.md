# Decisions and Open Questions

## 1. Locked product decisions

| Topic | Decision | Reason |
| --- | --- | --- |
| Comparison target | Complete CLI configurations | Pi, Claude Code, and Codex have different tools and harness behavior; calling this a pure model comparison would be misleading. |
| Initial language | Python only | Simple fixture creation, deterministic grading, and low setup cost. |
| Authentication | Existing CLI authentication | Avoids requiring new API keys and matches the user's actual working environment. |
| Execution environment | Fresh temporary repository copy | Provides repeatability and protects the source fixture without requiring Docker in the prototype. |
| Grading | Hidden deterministic tests and constraints | Produces explainable results without subjective LLM judging. |
| Suites | Three-case smoke subset plus eight-case core suite | Supports fast adapter validation and a meaningful first comparison without maintaining duplicate tasks. |
| Default attempts | One | Keeps the first run affordable and understandable. |
| Reliability mode | Optional three attempts | Gives variance and pass-rate stability when the user wants stronger evidence. |
| Scheduling | Sequential, randomized profile order | Produces more trustworthy latency comparisons on one laptop. |
| UI | Local interactive web dashboard | Best format for run control, live progress, charts, heatmaps, diffs, and traces. |
| Storage | Local SQLite plus filesystem artifacts | Minimal operations burden and sufficient for a single-user prototype. |
| Overall scoring | No opaque composite score | Correctness, cost, time, and reliability should remain inspectable tradeoffs. |
| Router analysis | Parse Pi JSON events | Pi assistant messages expose provider/model, response model, usage, and cost fields. |
| Jev and Laya | Excluded | The current concept compares CLI configurations directly. |
| Containers | Excluded from v1 | Temporary copies are enough for trusted prototype fixtures; strong isolation is deferred. |
| LLM judge | Excluded from v1 | Deterministic evidence is easier to trust and debug. |

## 2. Locked initial configurations

1. Pi AutoRouter
2. Claude Code Opus
3. Claude Code Sonnet
4. Claude Code Fable
5. Codex Sol
6. Codex Terra

Model names are configurable aliases. RouteBench must not assume that private aliases such as Fable, Sol, or Terra correspond to publicly documented model IDs.

## 3. Implementation-time validation spikes

These are not product-design blockers. They must be checked against the user's installed versions before full implementation.

### 3.1 Pi routing metadata

Run one harmless JSON-mode task and verify that `message_end.message.model`, `provider`, and optional `responseModel` reflect the route displayed in the TUI. If AutoRouter writes a custom extension event, preserve that event as additional route evidence.

Fallback: if the route cannot be extracted, compare Pi AutoRouter outcomes but hide route-distribution and route-overhead analytics.

### 3.2 Cost reporting

Inspect one structured output from every adapter without reading credential files.

- Pi is expected to report usage and cost.
- Claude Code may report total cost and per-model usage.
- Codex is expected to report token usage; dollar cost may require configured pricing.
- Private aliases may omit or override price metadata.

Fallback: show `Unavailable` rather than estimating without an explicit price table.

### 3.3 Exact command flags

Run each installed CLI's help command and verify supported non-interactive, model, output, permission, and session flags. Adapter commands must be version-aware and overridable in configuration.

### 3.4 Authentication readiness

Use safe CLI status checks when available. RouteBench must never open, copy, log, or parse credential stores.

## 4. Deferred product questions

These should not delay v1:

- Should later versions support JavaScript, TypeScript, Go, or mixed-language suites?
- Should Docker become the default isolation mechanism?
- Should RouteBench import SWE-bench, Aider Polyglot, or other public datasets?
- Should a later rubric include human review or an LLM judge as a clearly separate metric?
- Should the system support remote workers or CI execution?
- Should router-isolation mode compare AutoRouter against fixed models inside the same Pi harness?
- Should historical runs support statistical significance tests once enough repeated data exists?

## 5. Decisions intentionally left configurable

- Per-profile command template
- Model alias
- Allowed environment variables
- Timeout
- Attempt count
- Network policy label
- Price per million input, output, and cached tokens
- Workspace retention
- Maximum concurrency
- Test and grader commands
- Suite pass threshold

