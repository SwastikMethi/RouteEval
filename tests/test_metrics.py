"""Small offline examples pin ranking, missing-data, and routing semantics."""
import json
import unittest

from routebench.metrics import aggregate, router_summary


def run_for(*profiles, repeats=1):
    return {"status": "completed", "attempts_per_case": repeats,
            "profiles": [{"id": profile, "label": profile.upper(), "adapter": "pi" if profile == "router" else "codex",
                          "model": "Auto-3-API" if profile == "router" else "configured"} for profile in profiles],
            "suite": {"cases": [{"id": "one", "category": "bug"}, {"id": "two", "category": "feature"}]}}


def attempt(profile, case, score=1, *, index=0, status=None, cost=None, duration=None, routes=None, **extra):
    return {"id": f"{profile}-{case}-{index}", "profile_id": profile, "case_id": case, "attempt_index": index,
            "status": status or ("passed" if score == 1 else "failed"), "score": score, "full_pass": score == 1,
            "cost_usd": cost, "cost_provenance": "reported" if cost is not None else "unavailable",
            "agent_duration_ms": duration, "routes": routes or [], **extra}


class AggregateTests(unittest.TestCase):
    def test_macro_case_weighting_is_not_attempt_weighting(self):
        result = aggregate(run_for("a"), [attempt("a", "one"), attempt("a", "one", 0, index=1), attempt("a", "two")])
        row = result["leaderboard"][0]
        self.assertEqual(row["mean_score"], .75)
        self.assertEqual(row["median_score"], .75)
        self.assertAlmostEqual(row["pass_rate"], 2 / 3)
        self.assertEqual(row["total_attempts"], 3)
        self.assertEqual(result["categories"], [{"category": "bug", "profile_id": "a", "score": .5}, {"category": "feature", "profile_id": "a", "score": 1}])

    def test_missing_cost_and_tokens_stay_unknown(self):
        result = aggregate(run_for("a"), [attempt("a", "one"), attempt("a", "two")])
        row = result["leaderboard"][0]
        for field in ("total_cost_usd", "median_cost_usd", "quality_per_dollar", "input_tokens", "output_tokens", "tool_calls"):
            self.assertIsNone(row[field])
        self.assertEqual(result["metric_availability"], {"cost": 0, "route": 0})
        self.assertIsNone(result["winners"]["cheapest_successful"])
        self.assertFalse(result["provisional"])
        self.assertEqual(result["winners"]["best_quality"], "a")
        self.assertEqual(result["winners"]["best_pass_rate"], "a")

    def test_infrastructure_missing_and_scheduled_attempts_keep_reliability_denominators(self):
        result = aggregate(run_for("a", repeats=3), [attempt("a", "one"), attempt("a", "one", 0, index=1, status="timed_out"),
            attempt("a", "one", 1, index=2, status="infrastructure_error"), attempt("a", "two", None, status="queued")])
        row = result["leaderboard"][0]
        self.assertEqual(row["total_attempts"], 6)
        self.assertEqual(row["valid_attempts"], 2)
        self.assertAlmostEqual(row["pass_rate"], 1 / 6)
        self.assertAlmostEqual(row["timeout_rate"], 1 / 6)
        self.assertAlmostEqual(row["completion_rate"], 1 / 6)
        self.assertEqual(row["completed_pass_rate"], .5)
        self.assertEqual(row["mean_score"], .5)
        self.assertEqual(row["case_coverage"], .5)
        self.assertAlmostEqual(row["score_coverage"], 1 / 3)
        self.assertTrue(result["provisional"])
        self.assertTrue(all(value is None for value in result["winners"].values()))

    def test_invalid_scores_and_grader_infrastructure_are_never_quality(self):
        for invalid in (None, float("nan"), float("inf"), -1, 2, True, "1"):
            with self.subTest(invalid=invalid):
                result = aggregate(run_for("a"), [attempt("a", "one"), attempt("a", "two", invalid)])
                self.assertEqual(result["leaderboard"][0]["mean_score"], 1)
                self.assertEqual(result["leaderboard"][0]["valid_attempts"], 1)
                self.assertTrue(result["provisional"])
                json.dumps(result, allow_nan=False)
        result = aggregate(run_for("a"), [attempt("a", "one"), attempt("a", "two", graders=[{"infrastructure_error": True}])])
        self.assertTrue(result["provisional"])

    def test_winners_threshold_success_and_separate_pareto(self):
        attempts = []
        for profile, scores, cost, duration in [("a", (1, 1), 3, 3000), ("b", (1, .8), 1, 1000),
                                                ("c", (.6, .6), .2, 200), ("d", (1, .8), 2, 2000)]:
            attempts += [attempt(profile, case, score, cost=cost, duration=duration) for case, score in zip(("one", "two"), scores)]
        result = aggregate(run_for("a", "b", "c", "d"), attempts)
        self.assertEqual([row["profile_id"] for row in result["leaderboard"]], ["a", "b", "d", "c"])
        self.assertEqual(result["winners"]["best_quality"], "a")
        self.assertEqual(result["winners"]["fastest_successful"], "b")
        self.assertEqual(result["winners"]["cheapest_successful"], "b")
        self.assertEqual(result["winners"]["best_value"], "c")
        self.assertEqual(set(result["pareto"]["time"]), {"a", "b", "c"})
        self.assertEqual(set(result["pareto"]["cost"]), {"a", "b", "c"})
        self.assertEqual(result["quality_threshold"], .8)
        no_full_pass = aggregate(run_for("a"), [attempt("a", "one", .9, cost=1, duration=10), attempt("a", "two", .9, cost=1, duration=10)])
        self.assertIsNone(no_full_pass["winners"]["fastest_successful"])
        self.assertIsNone(no_full_pass["winners"]["cheapest_successful"])

    def test_cost_and_time_missing_coverage_exclude_efficient_labels(self):
        result = aggregate(run_for("a"), [attempt("a", "one", cost=.01, duration=1), attempt("a", "two")])
        row = result["leaderboard"][0]
        self.assertEqual(row["total_cost_usd"], .01)
        self.assertEqual(row["cost_coverage"], .5)
        self.assertEqual(row["time_coverage"], .5)
        self.assertIsNone(result["winners"]["cheapest_successful"])
        self.assertIsNone(result["winners"]["fastest_successful"])
        self.assertEqual(result["pareto"], {"cost": [], "time": []})

    def test_unknown_cost_provenance_is_not_trustworthy_and_zero_is_valid(self):
        untrusted = [attempt("a", "one", cost=1, cost_provenance="unavailable"), attempt("a", "two", cost=2, cost_provenance="unknown")]
        self.assertIsNone(aggregate(run_for("a"), untrusted)["leaderboard"][0]["total_cost_usd"])
        free = aggregate(run_for("a"), [attempt("a", "one", cost=0, duration=0, input_tokens=0), attempt("a", "two", cost=0, duration=0, input_tokens=0)])
        self.assertEqual(free["leaderboard"][0]["total_cost_usd"], 0)
        self.assertEqual(free["leaderboard"][0]["input_tokens"], 0)
        self.assertEqual(free["winners"]["cheapest_successful"], "a")
        self.assertIsNone(free["leaderboard"][0]["quality_per_dollar"])
        self.assertIsNone(free["leaderboard"][0]["quality_per_minute"])
        json.dumps(free, allow_nan=False)

    def test_repeated_case_variance_does_not_measure_case_difficulty(self):
        attempts = [attempt("a", case, score, index=index) for case, scores in [("one", (1, 0, 1)), ("two", (1, 1, 1))]
                    for index, score in enumerate(scores)]
        row = aggregate(run_for("a", repeats=3), attempts)["leaderboard"][0]
        self.assertAlmostEqual(row["mean_score"], 5 / 6)
        self.assertEqual(row["flake_rate"], .5)
        self.assertAlmostEqual(row["score_stddev"], 1 / 3)
        self.assertEqual(row["score_min"], 0)
        self.assertEqual(row["score_max"], 1)
        self.assertEqual([item["full_passes"] for item in row["repeat_variation"]], [2, 3])
        single = aggregate(run_for("a"), [attempt("a", "one"), attempt("a", "two", 0)])["leaderboard"][0]
        self.assertIsNone(single["score_stddev"])
        self.assertIsNone(single["flake_rate"])

    def test_ties_no_profiles_and_running_state(self):
        tied = aggregate(run_for("a", "b"), [attempt(profile, case, cost=1, duration=1) for profile in ("a", "b") for case in ("one", "two")])
        self.assertTrue(all(value is None for value in tied["winners"].values()))
        self.assertEqual(set(tied["pareto"]["cost"]), {"a", "b"})
        self.assertTrue(aggregate(run_for(), [])["provisional"])
        active = run_for("a")
        active["status"] = "running"
        result = aggregate(active, [attempt("a", "one"), attempt("a", "two")])
        self.assertTrue(result["provisional"])
        self.assertIsNone(result["winners"]["best_quality"])


class RouterTests(unittest.TestCase):
    def test_route_counts_messages_without_multiplying_attempt_costs(self):
        route_a = {"turn_index": 1, "provider": "provider", "model": "configured", "response_model": "A"}
        route_b = {"turn_index": 2, "provider": "provider", "model": "B"}
        attempts = [attempt("router", "one", cost=2, duration=100, routes=[route_a, route_a]),
                    attempt("router", "one", .2, index=1, cost=999, duration=999, routes=[route_a, route_b, route_b]),
                    attempt("router", "two", cost=.5, duration=50, routes=[route_b])]
        result = router_summary(run_for("router"), attempts)
        self.assertTrue(result["available"])
        rows = {row["model"]: row for row in result["distribution"]}
        self.assertEqual(rows["A"]["count"], 3)
        self.assertEqual(rows["B"]["count"], 3)
        self.assertEqual(rows["A"]["share"], .5)
        self.assertEqual(rows["A"]["total_cost_usd"], 2)
        self.assertEqual(rows["B"]["total_cost_usd"], .5)
        self.assertEqual(rows["A"]["median_agent_duration_ms"], 100)
        self.assertEqual(rows["B"]["median_agent_duration_ms"], 50)
        self.assertAlmostEqual(rows["A"]["mean_score"], .6)
        self.assertEqual(rows["A"]["associated_attempts"], 2)
        self.assertEqual(rows["A"]["single_route_attempts"], 1)
        self.assertEqual(len(result["timelines"]), 3)
        self.assertTrue(any(observation["type"] == "multi_route" for observation in result["observations"]))

    def test_all_multi_route_cost_and_time_remain_unavailable(self):
        result = router_summary(run_for("router"), [attempt("router", "one", cost=2, duration=100,
            routes=[{"model": "A"}, {"model": "B"}])])
        for row in result["distribution"]:
            self.assertIsNone(row["total_cost_usd"])
            self.assertIsNone(row["median_agent_duration_ms"])
        self.assertTrue(result["caveats"])

    def test_observations_require_comparable_outcomes_and_trusted_cost(self):
        attempts = [attempt("router", "one", .5, routes=[{"model": "observed"}]), attempt("baseline", "one", cost=1),
                    attempt("router", "two", cost=3, routes=[{"model": "observed"}]), attempt("baseline", "two", cost=1)]
        result = router_summary(run_for("router", "baseline"), attempts)
        self.assertEqual({entry["type"] for entry in result["observations"]}, {"potential_under_routing", "potential_over_routing"})
        attempts[2]["cost_provenance"] = "unavailable"
        result = router_summary(run_for("router", "baseline"), attempts)
        self.assertEqual({entry["type"] for entry in result["observations"]}, {"potential_under_routing"})
        attempts[0]["status"] = "infrastructure_error"
        self.assertEqual(router_summary(run_for("router", "baseline"), attempts)["observations"], [])

    def test_configured_alias_is_not_invented_as_an_observed_route(self):
        result = router_summary(run_for("router"), [attempt("router", "one", configured_model="Auto-3-API")])
        self.assertFalse(result["available"])
        self.assertEqual(result["distribution"], [])
        self.assertEqual(result["timelines"], [])


if __name__ == "__main__":
    unittest.main()
