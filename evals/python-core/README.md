# RouteBench Python Core 1.1.0

Nine isolated Python 3.11+ tasks. Runtime code uses only the standard library;
the harness supplies pytest. Smoke selects RB-PY-001, RB-PY-003, RB-PY-005 from
this manifest; Planning selects RB-PY-009 with the `planning` tag. Both subsets
reuse the same definitions as Core. suite.yaml uses the JSON
subset of YAML so release validation itself needs only the standard library.

Each case gives 80% to explicitly weighted behavior groups, 10% to visible
regression tests, 5% to source scope, and 5% to runtime constraints. Group scores are
binary: test count does not silently determine weight. All groups are mandatory
for full pass. RB-PY-007 intentionally starts behaviorally correct and fails its
mandatory structural refactoring group.

RB-PY-009 adds a single-session planning, implementation, and verification task.
Its inventory service requires atomic reservations, equivalent retries,
idempotent cancellation, and immutable records. The 5% runtime-contract group
also checks required sections in PLAN.md and VERIFICATION.md and the presence
of added pytest tests. It does not evaluate plan quality, certify self-reported
test results, enforce phase order, or reward routing changes. The documents and
added tests remain visible in the saved patch. Hidden tests independently check
implementation correctness.

Only fixtures/<case> enters an agent workspace. Hidden graders and reference
patches stay outside it. The runner copies hidden tests to __hidden__ only in
a separate grading workspace, substitutes {python}, disables plugin autoload,
and uses --tb=no --assert=plain. Ordinary output identifies failed test names
without rendering grader or reference source. These controls are benchmark
hygiene, not a hostile-code sandbox.

Reference patches apply with git apply from a clean fixture root. mutations.json
contains one deliberately wrong edit per reference and the defect it represents.
Run `.venv/bin/python -m pytest -q tests/test_eval_suite.py` from the project root
to validate metadata, scope, untouched baselines, references, and mutations.
Fixtures are intentionally concise; multi-file cases require actual traversal
of domain, configuration, service, or dispatch boundaries rather than padding.

No task requires model execution, network calls, credentials, wall-clock sleeps,
or dependency installation. Seeded generated input checks use fixed seeds.
The runner records its computed content hash with this semantic release version.
