# Research Notes

**Research date:** 2026-09-23  
**Scope:** Current non-interactive behavior needed to design RouteBench adapters. Installed-version behavior must still be verified locally during implementation.

## 1. Pi

### Findings

- Pi supports one-shot text output with `--print`.
- Pi supports JSONL events with `--mode json`.
- `--no-session` provides an in-memory session.
- `--model` accepts an exact or fuzzy model/provider pattern.
- JSON mode emits session, agent, turn, message, tool, retry, and compaction events.
- Completed assistant messages contain provider, model, optional response model, usage, and cost fields.
- Usage includes input, output, cache-read, cache-write, total tokens, and cost components.
- Pi's built-in tools run with process permissions; the temporary repository is not a strong security boundary.

### Design consequence

RouteBench can likely recover AutoRouter's selected provider/model from completed assistant messages even if the TUI is not captured. One local preflight run must confirm that the specific AutoRouter extension updates those fields consistently.

### Sources

- [Pi command-line reference](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/cli.md)
- [Pi JSON event stream](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/json.md)
- [Pi message types](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/message-types.md)

## 2. Claude Code

### Findings

- Programmatic execution uses print/headless mode through `claude -p`.
- JSON and streaming JSON output formats are available for structured automation.
- Structured results can include session/result metadata, usage, model usage, and total cost.
- Permission and allowed-tool flags support unattended repository work, but flag availability must be checked against the installed version.
- Existing CLI authentication can be reused; RouteBench does not need to collect a Claude API key.
- A bare mode is not the default design because it can change authentication and configuration behavior.

### Design consequence

Use streaming JSON when supported, preserve existing login state, and make the full command overridable for custom gateways or older versions.

### Sources

- [Claude Code programmatic usage](https://code.claude.com/docs/en/headless)
- [Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference)

## 3. Codex

### Findings

- Non-interactive execution uses `codex exec`.
- `--ephemeral` avoids persistent rollout files for an attempt.
- `--json` emits JSONL lifecycle and item events.
- `--sandbox workspace-write` allows repository edits with a constrained permission mode.
- Codex reuses saved CLI authentication by default.
- Codex expects to run inside a Git repository.
- Completed turns report token usage, while dollar-cost availability can vary.

### Design consequence

The workspace manager initializes Git before launch. RouteBench consumes JSONL and estimates cost only when explicit profile pricing exists.

### Source

- [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode)

## 4. Conclusions applied to the product

1. Existing CLIs are sufficient; separate model SDK integrations are unnecessary for v1.
2. Every CLI provides a workable machine-readable automation path.
3. Raw events must be retained because schemas and optional fields can vary by version.
4. Model aliases remain user-configurable.
5. Cost requires provenance because reporting differs by CLI and gateway.
6. The comparison must be described as configuration-level.
7. Temporary repository copies provide repeatability, not strong security isolation.
8. One preflight sample per installed CLI is required before relying on parser assumptions.

## 5. Research intentionally deferred

- Import formats for public benchmarks such as SWE-bench
- Cross-language fixture design
- Container isolation implementation
- Hosted/remote execution
- Statistical testing for large repeated runs
- LLM-judge calibration

These are outside the approved prototype scope and are not required before implementation begins.

