# CLI Adapter Specification

## 1. Purpose

Adapters let RouteBench drive Pi, Claude Code, Codex, and future coding agents through one normalized interface while preserving each CLI's native authentication and structured output.

Adapters are responsible for invocation and parsing. They do not grade code or calculate leaderboard metrics.

## 2. Adapter contract

```python
class AgentAdapter(Protocol):
    adapter_id: str

    def preflight(self, profile: Profile) -> PreflightResult: ...
    def build_command(self, context: AttemptContext) -> list[str]: ...
    def build_environment(self, context: AttemptContext) -> dict[str, str]: ...
    async def consume_stdout(self, line: bytes) -> list[NormalizedEvent]: ...
    async def consume_stderr(self, line: bytes) -> list[NormalizedEvent]: ...
    def finalize(self, process: ProcessResult) -> AgentResult: ...
```

Every adapter must support command overrides because installed versions and private model gateways can differ.

## 3. Common requirements

- Run non-interactively.
- Start in the attempt workspace.
- Start a new non-persistent session where supported.
- Emit or parse machine-readable output.
- Avoid interactive approval prompts.
- Use the least permissions needed to edit the workspace and run tests.
- Capture stdout and stderr concurrently.
- Preserve raw lines exactly in local artifacts.
- Parse lines incrementally; one malformed line must not discard the stream.
- Never pass credentials as command-line arguments.
- Never call commands that print API keys or bearer tokens.
- Never read CLI credential files.

## 4. Normalized event model

Every parsed record maps to zero or more normalized events:

```json
{
  "sequence": 27,
  "timestamp": "2026-09-23T12:00:03.410Z",
  "type": "tool.finished",
  "source": "pi",
  "attempt_id": "att_123",
  "turn_index": 2,
  "message_id": null,
  "tool": {
    "name": "bash",
    "call_id": "call_abc",
    "status": "succeeded",
    "summary": "python -m pytest"
  },
  "route": null,
  "usage": null,
  "raw_event_type": "tool_execution_end"
}
```

Normalized event types:

- `session.started`
- `turn.started`
- `turn.finished`
- `message.started`
- `message.delta`
- `message.finished`
- `tool.started`
- `tool.updated`
- `tool.finished`
- `file.changed`
- `retry.started`
- `retry.finished`
- `usage.updated`
- `route.observed`
- `agent.finished`
- `warning`
- `error`

## 5. Pi adapter

### 5.1 Expected command

Illustrative default:

```bash
pi --mode json --no-session --model <model-or-router-alias> -- <prompt>
```

The installed `pi --help` remains authoritative. The command is represented as an argument array.

### 5.2 Output

Pi JSON mode emits newline-delimited JSON and exits after the supplied prompt finishes. Important records include:

- `session`
- `agent_start` and `agent_end`
- `turn_start` and `turn_end`
- `message_start`, `message_update`, and `message_end`
- `tool_execution_start`, update, and end
- retry and compaction events

### 5.3 Route extraction

For assistant `message_end` records, read:

- `message.provider`
- `message.model`
- `message.responseModel`, when present
- `message.api`
- `message.usage`

Emit `route.observed` whenever a completed assistant message exposes a provider/model pair. If the pair changes across turns, preserve every observation and mark the attempt as multi-route.

If AutoRouter appends a custom session entry or extension event, store its structured details as supplementary routing evidence. Standard assistant-message fields remain the canonical fallback.

### 5.4 Usage and cost

Pi usage can include input, output, cache read, cache write, total tokens, and a cost object. To avoid double counting cumulative streaming updates, use the authoritative completed assistant message or deduplicate by message.

### 5.5 Security note

Pi tools execute with process permissions. A temporary repository is not a strong Pi sandbox. Only trusted fixtures are permitted in v1.

## 6. Claude Code adapter

### 6.1 Expected command

Illustrative default:

```bash
claude -p <prompt> \
  --output-format stream-json \
  --verbose \
  --model <model-alias> \
  --permission-mode acceptEdits \
  --permission-prompts none
```

Flag support must be checked during preflight. Profiles can override the command for older installations or custom gateways.

### 6.2 Authentication behavior

Do not use a mode that intentionally bypasses the user's saved login when the purpose is to reuse existing CLI authentication. RouteBench must not retrieve or copy Claude credentials.

### 6.3 Output parsing

Consume the stream JSON format and normalize:

- Assistant messages
- Tool calls and results
- Final result
- Usage
- Model usage
- Total cost when supplied
- Errors and permission failures

When per-model usage contains exactly one model, record it as the observed model. If several models appear, retain all usage entries and mark the attempt multi-model.

### 6.4 Permissions

The desired policy is unattended editing inside the disposable workspace without broad host access. `acceptEdits` is the preferred starting mode; exact allowed tools and prompt behavior must be verified against the installed version.

## 7. Codex adapter

### 7.1 Expected command

Illustrative default:

```bash
codex exec \
  --ephemeral \
  --json \
  --sandbox workspace-write \
  --model <model-alias> \
  <prompt>
```

Codex requires a Git repository; the workspace manager creates one before launch.

### 7.2 Authentication behavior

`codex exec` reuses saved CLI authentication by default. RouteBench must not inspect `auth.json` or copy it into workspaces.

### 7.3 Output parsing

Normalize JSONL events including:

- Thread and turn lifecycle
- Agent messages
- Command executions
- File changes
- MCP tool calls
- Errors
- Final usage from `turn.completed`

Expected token fields include input, cached input, output, and reasoning output when reported. The configured model alias is stored even when the event stream does not repeat it.

### 7.4 Cost

If Codex output does not include USD cost, RouteBench may estimate it only when the profile contains explicit, dated pricing. Otherwise cost remains unavailable.

## 8. Custom command adapter

For future CLIs, support an explicit command template:

```yaml
profiles:
  custom-agent:
    label: Custom Agent
    adapter: custom-jsonl
    model: custom-model
    command:
      - custom-agent
      - run
      - --json
      - --model
      - "{model}"
      - "{prompt}"
```

Supported placeholders:

- `{model}`
- `{prompt}`
- `{workspace}`
- `{attempt_id}`

Placeholders are expanded as individual arguments. Shell evaluation is prohibited.

## 9. Preflight contract

```json
{
  "profile_id": "pi-autorouter",
  "status": "ready",
  "executable": "pi",
  "resolved_path": "/usr/local/bin/pi",
  "version": "x.y.z",
  "structured_output": true,
  "authentication": "not_checked",
  "warnings": [],
  "capabilities": {
    "usage": true,
    "reported_cost": true,
    "route": true,
    "tool_events": true,
    "file_events": false
  }
}
```

Authentication states:

- `ready`, only when a safe CLI check establishes it
- `not_ready`, when a safe check reports failure
- `not_checked`, when no non-secret status check is available

## 10. Environment policy

Start from a minimal inherited environment containing ordinary runtime variables such as `PATH`, `HOME`, locale, and temporary-directory settings.

Variables whose names contain markers such as `KEY`, `TOKEN`, `SECRET`, `PASSWORD`, or `CREDENTIAL` are not inherited by default. A profile can explicitly allow a required variable, but the UI must warn that repository code may then access it.

Profile environment values must never appear in:

- Command previews
- Logs
- Events
- Database snapshots
- Exports
- Error messages

## 11. Parser resilience

- Preserve unrecognized JSON objects as raw events.
- Preserve non-JSON stdout as diagnostic text.
- Cap in-memory buffers and stream artifacts to disk.
- Record parser warnings with line sequence and reason.
- Never expose hidden reasoning content that the CLI does not intentionally provide.
- Avoid counting cumulative usage updates more than once.
- Prefer terminal/final usage records over intermediate values.

## 12. Adapter contract tests

Each adapter ships with sanitized fixture streams covering:

- Successful edit
- Tool call lifecycle
- Usage and cost
- Multiple turns
- Retry
- Timeout/partial output
- Provider error
- Malformed line
- Missing optional fields
- Multi-model route when supported

These tests must not call paid models.

