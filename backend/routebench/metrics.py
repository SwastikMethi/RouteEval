"""Deterministic configuration metrics; unavailable measurements stay unavailable."""

import math
from collections import Counter, defaultdict
from statistics import mean, median, pstdev, pvariance

from .costs import cost_metadata

TERMINAL = {"passed", "failed", "timed_out", "adapter_error", "infrastructure_error", "cancelled"}
WINNERS = (
    "best_quality",
    "best_pass_rate",
    "most_reliable",
    "fastest_successful",
    "cheapest_successful",
    "best_value",
    "best_time_efficiency",
)


def _number(value):
    return (
        value
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        else None
    )


def _score(attempt):
    value = _number(attempt.get("score"))
    if (
        attempt.get("status") not in TERMINAL - {"infrastructure_error", "cancelled"}
        or value is None
        or value > 1
    ):
        return None
    if any(grader.get("infrastructure_error") for grader in attempt.get("graders", [])):
        return None
    return value


def _passed(attempt):
    return (
        attempt.get("status") == "passed" and attempt.get("full_pass") is True and _score(attempt) is not None
    )


def _cost(attempt):
    return (
        _number(attempt.get("cost_usd"))
        if attempt.get("cost_provenance") in {"reported", "estimated"}
        else None
    )


def _values(attempts, field):
    return [value for attempt in attempts if (value := _number(attempt.get(field))) is not None]


def _macro(attempts):
    cases = defaultdict(list)
    for attempt in attempts:
        if (value := _score(attempt)) is not None:
            cases[attempt["case_id"]].append(value)
    return {case_id: mean(values) for case_id, values in cases.items()}


def _winner(rows, key, reverse=True):
    if not rows:
        return None
    ordered = sorted(rows, key=key, reverse=reverse)
    return ordered[0]["profile_id"] if len(ordered) == 1 or key(ordered[0]) != key(ordered[1]) else None


def _frontier(rows, metric):
    candidates = [
        row
        for row in rows
        if row["mean_score"] is not None
        and row[metric] is not None
        and row["score_coverage"] == 1
        and row["case_coverage"] == 1
    ]
    return [
        row["profile_id"]
        for row in candidates
        if not any(
            other["mean_score"] >= row["mean_score"]
            and other[metric] <= row[metric]
            and (other["mean_score"] > row["mean_score"] or other[metric] < row[metric])
            for other in candidates
        )
    ]


def aggregate(run: dict, attempts: list[dict]) -> dict:
    """Aggregate effective attempts. The caller removes superseded attempts first."""
    cases = {case["id"]: case for case in run.get("suite", {}).get("cases", [])}
    profiles = run.get("profiles", [])
    repeats = run.get("attempts_per_case", 1)
    repeats = repeats if isinstance(repeats, int) and repeats > 0 else 1
    threshold = _number(run.get("quality_threshold", 0.8))
    threshold = threshold if threshold is not None and threshold <= 1 else 0.8
    rows, categories = [], []
    total_scheduled = cost_available = route_available = 0
    provisional = run.get("status") in {"queued", "running"} or not profiles or not cases
    for profile in profiles:
        local = [
            attempt
            for attempt in attempts
            if attempt.get("profile_id") == profile["id"] and (not cases or attempt.get("case_id") in cases)
        ]
        scheduled = max(len(local), len(cases) * repeats)
        case_means = _macro(local)
        scored = [attempt for attempt in local if _score(attempt) is not None]
        full_passes = sum(_passed(attempt) for attempt in local)
        completed = sum(
            attempt.get("status") in {"passed", "failed"} and _score(attempt) is not None for attempt in local
        )
        started = sum(
            bool(attempt.get("started_at"))
            or attempt.get("status") in {"passed", "failed", "timed_out", "adapter_error"}
            for attempt in local
        )
        durations = _values(local, "agent_duration_ms")
        costs = [value for attempt in local if (value := _cost(attempt)) is not None]
        score_coverage = len(scored) / scheduled if scheduled else 0
        case_coverage = len(case_means) / len(cases) if cases else 0
        cost_coverage = len(costs) / scheduled if scheduled else 0
        time_coverage = len(durations) / scheduled if scheduled else 0
        provisional |= (
            score_coverage < 1
            or case_coverage < 1
            or any(attempt.get("status") not in TERMINAL for attempt in local)
        )
        total_scheduled += scheduled
        cost_available += len(costs)
        route_available += sum(bool(_models(attempt)) for attempt in local)
        quality = mean(case_means.values()) if case_means else None
        median_time = median(durations) if durations else None
        median_cost = median(costs) if costs else None
        variation = []
        for case_id in cases:
            repeated = [attempt for attempt in scored if attempt["case_id"] == case_id]
            scores = [_score(attempt) for attempt in repeated]
            if scores:
                variation.append(
                    {
                        "case_id": case_id,
                        "count": len(scores),
                        "mean": mean(scores),
                        "median": median(scores),
                        "min": min(scores),
                        "max": max(scores),
                        "stddev": pstdev(scores) if len(scores) > 1 else None,
                        "full_passes": sum(_passed(attempt) for attempt in repeated),
                    }
                )
        repeated_cases = [entry for entry in variation if entry["count"] > 1]
        flake_rate = (
            mean(0 < entry["full_passes"] < entry["count"] for entry in repeated_cases)
            if repeated_cases
            else None
        )
        variances = [
            pvariance([_score(attempt) for attempt in scored if attempt["case_id"] == entry["case_id"]])
            for entry in repeated_cases
        ]
        failures = Counter(
            attempt["status"] for attempt in local if attempt.get("status") in TERMINAL - {"passed"}
        )
        row = {
            **cost_metadata(local),
            "profile_id": profile["id"],
            "label": profile.get("label", profile["id"]),
            "mean_score": quality,
            "median_score": median(case_means.values()) if case_means else None,
            "full_passes": full_passes,
            "total_attempts": scheduled,
            "pass_rate": full_passes / scheduled if scheduled else 0,
            "completion_rate": completed / scheduled if scheduled else 0,
            "completed_pass_rate": full_passes / started if started else None,
            "median_agent_duration_ms": median_time,
            "total_cost_usd": sum(costs) if costs else None,
            "median_cost_usd": median_cost,
            "cost_coverage": cost_coverage,
            "time_coverage": time_coverage,
            "score_coverage": score_coverage,
            "case_coverage": case_coverage,
            "timeout_rate": failures["timed_out"] / scheduled if scheduled else 0,
            "adapter_error_rate": failures["adapter_error"] / scheduled if scheduled else 0,
            "flake_rate": flake_rate,
            "score_stddev": math.sqrt(mean(variances)) if variances else None,
            "score_min": min((_score(attempt) for attempt in scored), default=None),
            "score_max": max((_score(attempt) for attempt in scored), default=None),
            "quality_per_dollar": quality / median_cost
            if quality is not None and median_cost and cost_coverage == 1
            else None,
            "quality_per_minute": quality * 60000 / median_time
            if quality is not None and median_time and time_coverage == 1
            else None,
            "valid_attempts": len(scored),
            "failures": dict(failures),
            "repeat_variation": variation,
        }
        for field in ("input_tokens", "output_tokens", "tool_calls"):
            values = _values(local, field)
            row[field] = sum(values) if values else None
        rows.append(row)
        for category in sorted({case.get("category", "uncategorized") for case in cases.values()}):
            scores = [
                score
                for case_id, score in case_means.items()
                if cases[case_id].get("category", "uncategorized") == category
            ]
            categories.append(
                {"category": category, "profile_id": profile["id"], "score": mean(scores) if scores else None}
            )
    rows.sort(
        key=lambda row: (
            -(row["mean_score"] if row["mean_score"] is not None else -1),
            -row["pass_rate"],
            -row["completion_rate"],
            row["median_agent_duration_ms"] if row["median_agent_duration_ms"] is not None else math.inf,
            row["profile_id"],
        )
    )
    winners = dict.fromkeys(WINNERS)
    with_scores = [row for row in rows if row["mean_score"] is not None]
    successful = [row for row in with_scores if row["mean_score"] >= threshold and row["full_passes"] > 0]
    time_rows = [row for row in with_scores if row["time_coverage"] == 1]
    cost_rows = [row for row in with_scores if row["cost_coverage"] == 1]
    if not provisional:
        winners.update(
            best_quality=_winner(with_scores, lambda row: (row["mean_score"], row["pass_rate"])),
            best_pass_rate=_winner(with_scores, lambda row: row["pass_rate"]),
            most_reliable=_winner(
                with_scores, lambda row: (row["completion_rate"], -(row["flake_rate"] or 0))
            ),
            fastest_successful=_winner(
                [row for row in successful if row in time_rows],
                lambda row: row["median_agent_duration_ms"],
                False,
            ),
            cheapest_successful=_winner(
                [row for row in successful if row in cost_rows], lambda row: row["median_cost_usd"], False
            ),
            best_value=_winner(
                [row for row in cost_rows if row["quality_per_dollar"] is not None],
                lambda row: row["quality_per_dollar"],
            ),
            best_time_efficiency=_winner(
                [row for row in time_rows if row["quality_per_minute"] is not None],
                lambda row: row["quality_per_minute"],
            ),
        )
    return {
        "leaderboard": rows,
        "winners": winners,
        "categories": categories,
        "pareto": {
            "time": _frontier(time_rows, "median_agent_duration_ms"),
            "cost": _frontier(cost_rows, "median_cost_usd"),
        },
        "metric_availability": {
            "cost": cost_available / total_scheduled if total_scheduled else 0,
            "route": route_available / total_scheduled if total_scheduled else 0,
        },
        "provisional": bool(provisional),
        "quality_threshold": threshold,
    }


def _models(attempt):
    return [
        model
        for route in attempt.get("routes", [])
        if isinstance(route, dict)
        and isinstance(
            model := route.get("response_model") or route.get("responseModel") or route.get("model"), str
        )
        and model
    ]


def router_summary(run: dict, attempts: list[dict]) -> dict:
    """Count route observations, associating attempt outcomes without duplicating their costs."""
    cases = {case["id"]: case for case in run.get("suite", {}).get("cases", [])}
    profiles = {profile["id"]: profile for profile in run.get("profiles", [])}
    counts, category_counts = Counter(), Counter()
    associated, single_route = defaultdict(list), defaultdict(list)
    timelines, observations = [], []
    routed = []
    for attempt in attempts:
        models = _models(attempt)
        if not models:
            continue
        counts.update(models)
        category = cases.get(attempt["case_id"], {}).get("category", "uncategorized")
        category_counts.update((category, model) for model in models)
        timelines.append(
            {"attempt_id": attempt["id"], "case_id": attempt["case_id"], "routes": attempt.get("routes", [])}
        )
        for model in set(models):
            associated[model].append(attempt)
        if len(set(models)) == 1:
            single_route[models[0]].append(attempt)
        else:
            observations.append(
                {
                    "case_id": attempt["case_id"],
                    "type": "multi_route",
                    "message": "Multiple models were observed in this attempt. Its score is associated with each model; its whole-attempt time and cost are excluded from model totals.",
                }
            )
        profile = profiles.get(attempt["profile_id"], {})
        if profile.get("adapter") == "pi" and any(
            marker in str(profile.get("model", "")).lower() for marker in ("auto", "router")
        ):
            routed.append(attempt)
    total = sum(counts.values())
    distribution = []
    for model, count in counts.most_common():
        related, single = associated[model], single_route[model]
        quality = _macro(related)
        durations = _values(single, "agent_duration_ms")
        costs = [value for attempt in single if (value := _cost(attempt)) is not None]
        distribution.append(
            {
                "model": model,
                "count": count,
                "share": count / total,
                "mean_score": mean(quality.values()) if quality else None,
                "pass_rate": sum(_passed(attempt) for attempt in related) / len(related),
                "median_agent_duration_ms": median(durations) if durations else None,
                "total_cost_usd": sum(costs) if costs else None,
                "associated_attempts": len(related),
                "single_route_attempts": len(single),
                "cost_coverage": len(costs) / len(single) if single else 0,
                "time_coverage": len(durations) / len(single) if single else 0,
            }
        )
    seen = set()
    for attempt in routed:
        baselines = [
            other
            for other in attempts
            if other.get("case_id") == attempt["case_id"]
            and other.get("profile_id") != attempt["profile_id"]
            and _passed(other)
        ]
        if not baselines or _score(attempt) is None:
            continue
        kind = None
        if not _passed(attempt):
            kind = "potential_under_routing"
            message = "A routed attempt did not fully pass while another configuration fully passed this case. This is an observed outcome difference, not proof that a selected model was weaker."
        else:
            cost = _cost(attempt)
            if cost is not None and any(
                _cost(other) is not None and _cost(other) < cost for other in baselines
            ):
                kind = "potential_over_routing"
                message = "A routed attempt cost more than another configuration that fully passed this case. Whole-attempt costs and different harnesses cannot establish routing regret."
        if kind and (attempt["case_id"], kind) not in seen:
            seen.add((attempt["case_id"], kind))
            observations.append({"case_id": attempt["case_id"], "type": kind, "message": message})
    return {
        "available": bool(total),
        "distribution": distribution,
        "timelines": timelines,
        "observations": observations,
        "categories": [
            {"category": category, "model": model, "count": count}
            for (category, model), count in sorted(category_counts.items())
        ],
        "caveats": [
            "Counts are observed route records, not attempts. Outcome associations do not establish model causality.",
            "Model time and cost aggregates include each single-route attempt once; multi-route attempts are excluded.",
            "Comparisons across configurations include different CLI tools, prompts, permissions, and serving behavior.",
        ],
    }
