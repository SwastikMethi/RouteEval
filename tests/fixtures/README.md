# Adapter stream fixtures

All `*.synthetic.jsonl` files here are hand-authored, sanitized protocol examples,
not recordings of paid model runs. They test parsing and cannot establish live
authentication, routing availability, pricing, or tool permissions.

The Pi shapes were cross-checked against installed 0.84.4 `json-event` and
`agent-session` public type declarations. Claude and Codex examples use their
stream-json/exec JSONL message shapes. Each fixture deliberately includes a
non-JSON diagnostic line to exercise parser recovery, and reasoning markers to
verify they never become normalized display events.

Separately, local non-paid `--version`/`--help` checks on 2026-09-23 observed
Pi 0.84.4, Claude Code 2.1.280, and Codex 0.156.1 and the required invocation flags.
Those checks did not read credential files or make model requests.

The six `*.actual.jsonl` files are **minimized actual outputs** from the user's
authorized live checks on 2026-09-23. `actual-provenance.json` records CLI versions,
capture timestamps, source artifact paths/hashes, exit codes, and transformations.
Source hashes describe the recordings at fixture capture time. Later privacy
scrubbing of retained local artifacts updates their database hashes; it does not
change these minimized fixtures or their capture provenance.
They cover Pi Auto-3-API success (observed response model `gpt-5.6-sol`), Claude
Sonnet success, Claude Opus/Fable provider rejections, and Codex Sol/Terra alias
rejections. They do not claim successful Codex execution or support for rejected
aliases. Creating and testing these fixtures made no additional model calls.

Actual fixtures preserve authoritative usage values and protocol semantics while
dropping reasoning content, tool arguments/results/file contents, startup config
inventory, private paths, and session metadata. IDs were consistently remapped;
each retained event passed through the application's sanitizer. Numeric reasoning
token counts remain as reported usage, without reasoning text. The Pi zero cost is
the CLI-reported value, not a claim about actual provider billing.
