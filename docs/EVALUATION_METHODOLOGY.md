# Evaluation Methodology

## 1. Purpose

This document defines how RouteBench produces a fair, reproducible, and interpretable comparison of coding-agent configurations.

The unit being evaluated is:

> CLI + configured model or router + system behavior + tools + permissions + runtime settings

It is not an isolated model evaluation.

## 2. Experimental unit

One **attempt** is one configured agent executing one case once in a fresh repository workspace.

An attempt is uniquely identified by:

- Run ID
- Suite version
- Case ID
- Profile ID
- Attempt index
- Fixture commit or content hash
- Canonical prompt hash
- CLI version
- Configuration fingerprint

## 3. Fairness protocol

### 3.1 Controlled inputs

Every profile receives:

- The same fixture content
- The same user prompt
- The same task-visible files
- The same dependency state
- The same timeout
- The same host resource policy
- The same network policy label
- A fresh agent session

### 3.2 Intentionally uncontrolled variables

The following remain part of the tested configuration:

- CLI system prompt
- Built-in tool implementation
- Context-management strategy
- Retry behavior
- Model or router behavior
- Provider-side serving behavior

These variables are why RouteBench reports configuration comparisons.

### 3.3 Execution order

Default mode is sequential.

For each case:

1. Randomize profile order using a run seed.
2. Execute one profile at a time.
3. Record the order and timestamps.

This reduces systematic bias caused by always running one provider first. Optional parallel mode is allowed for demonstrations, but the UI must mark its latency measurements as contention-affected.

### 3.4 Timing boundaries

Record separate clocks:

| Metric | Start | Stop |
| --- | --- | --- |
| Preparation duration | Workspace-copy start | Agent process launch |
| Agent duration | Agent process launch | Agent process exit or timeout |
| Grading duration | First grader start | Last grader finish |
| Attempt duration | Workspace-copy start | Results persisted |
| Run elapsed time | First attempt preparation | Last attempt persisted |

Only **agent duration** is used for agent speed comparisons.

### 3.5 Repetition

- Default prototype run: one attempt per case/profile.
- Reliability run: three attempts per case/profile.
- The UI must not claim statistical significance from one attempt.
- When repeats exist, display mean, median, range, standard deviation, and success count.

## 4. Case grading model

### 4.1 Components

Default component weights:

| Component | Weight | Examples |
| --- | ---: | --- |
| Functional correctness | 0.80 | Hidden behavior tests, edge cases, required outputs |
| Regression safety | 0.10 | Existing behavior, API compatibility, unrelated tests |
| Constraints and deterministic quality | 0.10 | Forbidden edits, lint, types, scope limits |

Each component consists of one or more graders. Graders emit a score from `0.0` to `1.0`, a pass state, evidence, and whether they are mandatory.

### 4.2 Task score

For case \(c\) and attempt \(a\):

$$
S_{a,c} = \sum_{k=1}^{n} w_k g_k
$$

where \(g_k\) is a grader score and the non-negative weights \(w_k\) sum to 1.

### 4.3 Full pass

An attempt receives a full pass only when:

- Its task score meets the case threshold, default `1.0`.
- Every mandatory grader passes.
- No forbidden test or grader file was changed.
- The agent process did not time out or end in an unrecoverable adapter error.

### 4.4 Configuration score

Overall quality is the macro-average across cases:

$$
Q_p = \frac{1}{|C|}\sum_{c \in C} \overline{S}_{p,c}
$$

Each case receives equal weight, regardless of how many individual tests it contains. When multiple attempts exist, \(\overline{S}_{p,c}\) is the mean score for that profile and case.

### 4.5 Pass rate

$$
PassRate_p = \frac{\text{full passes for profile }p}{\text{total scheduled attempts for }p}
$$

Also report **completed-pass rate**, which excludes attempts that never started. The primary UI uses scheduled-attempt pass rate so crashes and timeouts remain visible.

## 5. Metric catalog

### 5.1 Primary quality metrics

| Metric | Definition | Interpretation |
| --- | --- | --- |
| Mean task score | Macro-average of task scores | Partial and full correctness |
| Full pass rate | Full passes / scheduled attempts | Strict end-to-end success |
| Mandatory-check failure rate | Attempts with any required grader failure | Serious correctness or safety failure |
| Category score | Macro-average within a task category | Strengths by task type |
| Difficulty score | Macro-average by difficulty | Performance as complexity increases |

### 5.2 Reliability metrics

| Metric | Definition |
| --- | --- |
| Completion rate | Attempts reaching grading without adapter failure or timeout |
| Timeout rate | Timed-out attempts / scheduled attempts |
| Adapter error rate | CLI invocation or parser failures / scheduled attempts |
| Flake rate | Cases with mixed pass/fail outcomes across repeated attempts |
| Score variance | Variance across repeated attempts for the same profile/case |

### 5.3 Time metrics

- Agent wall-clock duration
- Time to first structured agent event
- Time to first tool call
- Grading duration
- End-to-end attempt duration
- Total suite elapsed time

Time to first event is diagnostic. It should not be treated as model generation latency because CLI startup and router setup may contribute.

### 5.4 Usage metrics

- Input tokens
- Output tokens
- Cached-read tokens
- Cache-write tokens when reported
- Reasoning tokens when separately reported
- Total provider-reported tokens
- Tool-call count
- Turn count
- Retry count

Tokens from different providers or tokenizers are not perfectly comparable. They are presented as reported usage, not a universal unit of work.

### 5.5 Cost metrics

Every cost value includes provenance:

- `reported`: supplied by CLI/provider output
- `estimated`: calculated from explicit profile prices
- `unavailable`: insufficient trustworthy information

Estimated cost:

$$
Cost = \frac{T_{in}P_{in} + T_{out}P_{out} + T_{cache}P_{cache}}{1{,}000{,}000}
$$

The timestamp and source of the configured price must be stored. Reported and estimated cost must be visually distinguishable.

### 5.6 Patch metrics

- Files changed
- Lines added and removed
- Test files changed
- Forbidden files changed
- Patch size per passing attempt
- Untracked files created

Smaller patches are not automatically better. Patch metrics are diagnostic and only become negative when a case defines explicit scope constraints.

## 6. Ranking and winner labels

### 6.1 Primary leaderboard

Default order:

1. Mean task score, descending
2. Full pass rate, descending
3. Completion rate, descending
4. Median agent duration, ascending

### 6.2 Objective-specific winners

| Label | Selection rule |
| --- | --- |
| Best quality | Highest mean task score, then full pass rate |
| Most reliable | Highest completion rate, then lowest flake rate |
| Fastest successful | Lowest median agent duration among profiles above the quality threshold |
| Cheapest successful | Lowest trustworthy cost among profiles above the quality threshold |
| Best value | Highest quality per dollar among profiles with trustworthy cost |
| Best time efficiency | Highest quality per agent minute |

The quality threshold defaults to a mean task score of `0.8` and must be visible.

### 6.3 Pareto frontier

A profile is Pareto-efficient when no other profile is simultaneously:

- Equal or better in quality, and
- Equal or lower in cost or time,

with at least one strict improvement.

Cost and time receive separate frontier charts.

## 7. AutoRouter metrics

### 7.1 Route extraction

For every completed Pi assistant message, collect:

- Requested model/router alias
- `provider`
- `model`
- Optional `responseModel`
- Message usage and cost
- Message start/end timestamps

If the extension emits custom route metadata, store it as additional evidence without replacing standard message fields.

### 7.2 Router views

- Selection count and share by model
- Quality by selected model
- Full pass rate by selected model
- Time and cost by selected model
- Selection by category and difficulty
- Number of model changes within an attempt
- Router or first-response startup time when measurable

### 7.3 Over-routing and under-routing

For cross-CLI results, use cautious labels:

- **Potential over-routing:** AutoRouter selected a more expensive route while a cheaper observed baseline fully passed.
- **Potential under-routing:** AutoRouter failed while a stronger observed baseline fully passed.

These are observations, not causal proof, because the baselines use different harnesses.

True routing regret should only be calculated later when AutoRouter and fixed alternatives run through the same Pi harness:

$$
Regret = U(oracle\ route) - U(selected\ route)
$$

where the utility function and price assumptions are declared before the run.

## 8. Data validity rules

An attempt is excluded from a specific metric only when that metric is genuinely unavailable. It remains in reliability denominators.

Examples:

- Missing cost: exclude from cost averages; keep in quality and completion metrics.
- Timeout: score as failed according to case policy; include in timeout and scheduled pass-rate denominators.
- Grader infrastructure failure: mark invalid rather than agent failure, then rerun after repair.
- Fixture baseline failure: invalidate the case before running any profiles.

## 9. Suite validation

Before publishing a suite version:

1. Confirm the baseline fixture fails its target graders.
2. Confirm the reference solution passes all graders.
3. Confirm hidden tests are not visible from the agent workspace.
4. Confirm graders do not require network access.
5. Confirm the case runs from a clean machine environment.
6. Confirm no case depends on secret values.
7. Review difficulty and category labels.
8. Freeze a content hash and semantic suite version.

## 10. Interpretation rules

- Do not generalize an eight-case result to all software engineering.
- Do not call a one-attempt difference statistically significant.
- Do not compare dollar cost when one profile has unavailable or stale pricing.
- Do not treat tokens from different providers as identical computational units.
- Do not infer pure model quality from configuration-level results.
- Always include suite version, case count, attempts, timeout, and run mode in exported summaries.

