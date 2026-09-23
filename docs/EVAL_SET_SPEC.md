# Python Eval-Set Specification

## 1. Objective

Create a small, custom, deterministic coding benchmark that exercises different software-engineering behaviors without requiring external services, large dependency installations, or subjective judging.

The core suite contains eight cases. The smoke suite is a three-case tag-based subset of the same suite, so there is only one source of truth.

## 2. Suite composition

| Category | Count | Primary capability |
| --- | ---: | --- |
| Bug fixing | 2 | Diagnose failing behavior and implement a minimal correction |
| Feature implementation | 2 | Add specified behavior and edge-case handling |
| Multi-file repository work | 2 | Navigate relationships and coordinate changes across modules |
| Constrained refactoring | 1 | Improve structure without changing public behavior |
| Edge-case reasoning | 1 | Handle security or correctness boundaries that simple happy-path code misses |

Difficulty distribution:

- Easy: 3
- Medium: 3
- Hard: 2

## 3. Initial case inventory

### RB-PY-001 — Decimal average correction

| Attribute | Value |
| --- | --- |
| Category | Bug fixing |
| Difficulty | Easy |
| Smoke suite | Yes |
| Fixture size | Approximately 150–250 lines |

Scenario: a small invoice utility calculates averages using integer division and mishandles decimal values and empty collections.

Agent task:

- Correct the average calculation.
- Preserve the public function signature.
- Return the documented empty-input value.
- Do not alter visible tests.

Hidden coverage:

- Positive integers
- Decimals
- Negative values
- Empty input
- Input immutability

Constraints:

- Existing API remains unchanged.
- Test files may not be edited.

### RB-PY-002 — Retry-After parser

| Attribute | Value |
| --- | --- |
| Category | Bug fixing |
| Difficulty | Medium |
| Smoke suite | No |
| Fixture size | Approximately 250–400 lines |

Scenario: an HTTP retry helper accepts integer seconds but mishandles whitespace, invalid values, and HTTP-date forms.

Agent task:

- Fix parsing according to the fixture's documented behavior.
- Preserve fallback behavior.
- Avoid introducing network calls.

Hidden coverage:

- Integer values
- Leading/trailing whitespace
- Invalid and negative values
- HTTP-date in past and future
- Timezone handling through an injected clock

Constraints:

- Standard library only.
- No wall-clock dependency in tests.

### RB-PY-003 — Inventory filtering feature

| Attribute | Value |
| --- | --- |
| Category | Feature implementation |
| Difficulty | Easy |
| Smoke suite | Yes |
| Fixture size | Approximately 250–350 lines |

Scenario: a small inventory package needs composable filtering by category, availability, and optional maximum price.

Agent task:

- Implement the documented filter method.
- Keep input order stable.
- Avoid mutating inventory records.

Hidden coverage:

- Every filter independently
- Combined filters
- Empty input
- `None` values
- Stable ordering
- Input immutability

Constraints:

- No third-party dependencies.
- Public records and constructor signatures remain unchanged.

### RB-PY-004 — TTL cache feature

| Attribute | Value |
| --- | --- |
| Category | Feature implementation |
| Difficulty | Medium |
| Smoke suite | No |
| Fixture size | Approximately 300–450 lines |

Scenario: extend an existing in-memory cache with per-item TTL and an injectable clock.

Agent task:

- Add TTL support.
- Preserve behavior for entries without TTL.
- Lazily remove expired entries.
- Expose no background thread.

Hidden coverage:

- Immediate, boundary, and delayed expiration
- Overwrite behavior
- Non-expiring entries
- `get`, membership, and size consistency
- Injectable clock

Constraints:

- No sleeping in tests or implementation.
- Existing no-TTL API remains compatible.

### RB-PY-005 — Layered configuration resolution

| Attribute | Value |
| --- | --- |
| Category | Multi-file repository work |
| Difficulty | Hard |
| Smoke suite | Yes |
| Fixture size | Approximately 500–750 lines across modules |

Scenario: a package merges defaults, project configuration, environment overrides, and explicit runtime values, but precedence and type coercion are inconsistent across modules.

Agent task:

- Implement the documented precedence order.
- Centralize coercion without breaking existing callers.
- Return useful validation errors.

Hidden coverage:

- Every precedence pair
- Boolean and integer coercion
- Missing values
- Invalid values
- Nested settings
- Public API compatibility

Constraints:

- Changes may span the documented config modules only.
- Environment access must remain injectable.

### RB-PY-006 — Event export across layers

| Attribute | Value |
| --- | --- |
| Category | Multi-file repository work |
| Difficulty | Medium |
| Smoke suite | No |
| Fixture size | Approximately 450–650 lines |

Scenario: add CSV export to a service with domain, serialization, and CLI layers.

Agent task:

- Add export through the existing service boundary.
- Produce deterministic column order and escaping.
- Wire one new CLI option without changing existing commands.

Hidden coverage:

- Empty and populated exports
- Commas, quotes, and newline escaping
- Unicode
- Stable headers
- Existing CLI behavior

Constraints:

- Use the standard library CSV implementation.
- Do not place domain logic directly in the CLI layer.

### RB-PY-007 — Transport registry refactor

| Attribute | Value |
| --- | --- |
| Category | Constrained refactoring |
| Difficulty | Hard |
| Smoke suite | No |
| Fixture size | Approximately 650–900 lines |

Scenario: three duplicated transport branches must be converted into a registry while preserving public behavior and exception types.

Agent task:

- Introduce one internal registry abstraction.
- Remove duplicated selection branches.
- Preserve all public APIs and errors.

Hidden coverage:

- Existing transports
- Unknown transport errors
- Registration collisions
- Construction arguments
- Import compatibility

Constraints:

- No new runtime dependency.
- No test edits.
- A deterministic structure check verifies that the duplicated branch pattern is removed.

### RB-PY-008 — Safe path normalization

| Attribute | Value |
| --- | --- |
| Category | Edge-case reasoning |
| Difficulty | Easy |
| Smoke suite | No |
| Fixture size | Approximately 150–250 lines |

Scenario: normalize user-supplied relative artifact paths while rejecting traversal and ambiguous absolute paths.

Agent task:

- Implement the documented path rules.
- Support ordinary nested relative paths.
- Reject paths escaping the configured root.

Hidden coverage:

- Nested relative paths
- `..` traversal
- Dot segments
- Absolute POSIX and Windows-like paths
- Prefix collisions
- Empty and Unicode names

Constraints:

- Do not rely on string-prefix containment checks.
- Standard library only.

## 4. Suite manifest

Illustrative schema:

```yaml
version: 1
id: python-core
release: 1.0.0
name: RouteBench Python Core
description: Eight deterministic coding-agent cases.
pass_threshold: 1.0

cases:
  - id: RB-PY-001
    title: Decimal average correction
    category: bug-fix
    difficulty: easy
    tags: [python, smoke, correctness]
    fixture: fixtures/RB-PY-001
    hidden_graders: hidden-graders/RB-PY-001
    reference_patch: reference-solutions/RB-PY-001.patch
    timeout_seconds: 600
    prompt: |
      The invoice average calculation is producing incorrect results.
      Fix the behavior described in README.md, preserve the public API,
      and run the relevant tests.
    scoring:
      - component: functional
        weight: 0.80
        command: [python, -m, pytest, -q, .routebench_hidden/functional]
        mandatory: true
      - component: regression
        weight: 0.10
        command: [python, -m, pytest, -q, tests]
        mandatory: true
      - component: constraints
        weight: 0.10
        grader: forbidden_paths
        paths: [tests/**]
        mandatory: true
```

## 5. Fixture contract

Every fixture includes:

- README with only task-relevant repository documentation
- Source package
- Visible baseline tests where appropriate
- Dependency declaration or standard-library-only marker
- `.gitignore`
- No hidden grader or reference solution
- No secrets, personal data, or network dependency

Recommended fixture size is 150–900 lines of relevant source code. Fixtures should be large enough to require navigation but small enough for a prototype run.

## 6. Hidden grader contract

Hidden grader directory may contain:

- Additional pytest tests
- Deterministic static checks
- Allowed/forbidden path policy
- Expected public API snapshot
- Test-group metadata for partial scores

Hidden graders must not:

- Use network access
- Depend on wall-clock time without an injected clock
- Depend on test order
- Read host credentials
- Reveal reference code through routine assertion output
- Reward one particular implementation when behavior and constraints are satisfied

## 7. Reference solution contract

Every case includes one reviewed reference patch that:

- Passes every grader from a clean fixture.
- Makes a reasonably scoped change.
- Demonstrates that the case is solvable within the timeout.
- Is not copied into the agent workspace.

The reference patch is validation evidence, not a canonical implementation the agent must reproduce.

## 8. Baseline and mutation checks

For every suite release:

- The untouched fixture must fail at least one mandatory functional grader.
- The reference solution must fully pass.
- Where practical, simple incorrect mutations should fail hidden tests.
- Visible tests alone must not be enough to game the task by returning constants.

## 9. Scoring details

Within a component, tests can be grouped by behavior. For example:

```yaml
groups:
  basic: 0.30
  edge_cases: 0.40
  invariants: 0.30
```

This creates meaningful partial credit. The number of individual test functions must not determine weight accidentally.

## 10. Anti-cheating rules

- Editing visible test files is forbidden unless the case explicitly requests tests.
- Creating local replacements for grader modules is forbidden.
- Hardcoded fixture-specific outputs should fail randomized or parameterized hidden cases.
- Graders execute from an external overlay.
- The canonical prompt does not reveal grader names or file paths.
- The system stores pre- and post-run Git trees for audit.

## 11. Suite release checklist

- [ ] IDs and metadata are valid.
- [ ] Smoke tags select exactly RB-PY-001, RB-PY-003, and RB-PY-005.
- [ ] Fixtures contain no symlinks.
- [ ] Fixtures install or run offline.
- [ ] Baselines fail for the intended reason.
- [ ] Reference solutions pass.
- [ ] Hidden tests are agent-invisible.
- [ ] Grader output is sanitized.
- [ ] Scores use explicit behavior-group weights.
- [ ] Case runtimes are comfortably below timeout.
- [ ] Suite content hash and version are frozen.

