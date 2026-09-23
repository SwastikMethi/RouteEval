"""Offline adapter contract checks. No test calls an installed model CLI."""

import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from routebench.adapters import AdapterParser, _environment, build_command, preflight

FIXTURES = Path(__file__).parent / "fixtures"


def parse(adapter, objects, exit_code=0):
    parser = AdapterParser(adapter, "configured-alias")
    events = []
    for obj in objects:
        events.extend(parser.feed(obj if isinstance(obj, str) else json.dumps(obj)))
    return parser.finalize(exit_code), events


class AdapterTests(unittest.TestCase):
    def test_synthetic_success_streams(self):
        for adapter in ("pi", "claude", "codex"):
            with self.subTest(adapter=adapter):
                result, events = parse(
                    adapter, (FIXTURES / f"{adapter}.synthetic.jsonl").read_text().splitlines()
                )
                self.assertIsNone(result["error_code"])
                self.assertEqual(result["input_tokens"], 30)
                self.assertEqual(result["output_tokens"], 12)
                self.assertEqual(result["cached_input_tokens"], 7)
                self.assertEqual(result["input_includes_cache"], adapter == "codex")
                self.assertEqual(result["final_response"], "Implemented; tests pass.")
                self.assertEqual(result["retries"], 1)
                self.assertEqual(result["tool_calls"], 3 if adapter == "codex" else 1)
                self.assertEqual(result["turns"], 3 if adapter == "pi" else 2)
                self.assertEqual(len(result["parser_warnings"]), 1)
                self.assertNotIn("PRIVATE_REASONING_MARKER", json.dumps(events))
                self.assertNotIn("PRIVATE_REASONING_MARKER", result["final_response"])
                self.assertIsNone(result["primary_observed_model"])
                self.assertEqual(
                    result["cost_provenance"], "unavailable" if adapter == "codex" else "reported"
                )
                self.assertEqual(result["cost_usd"], {"pi": 0.008, "claude": 0.009, "codex": None}[adapter])
                if adapter == "codex":
                    self.assertEqual(result["routes"], [])
                    self.assertTrue(any(event["type"] == "file.changed" for event in events))
                else:
                    self.assertGreaterEqual(len(result["routes"]), 2)

    def test_terminal_event_never_substitutes_for_process_exit(self):
        for adapter, terminal in [
            ("pi", {"type": "agent_end"}),
            ("claude", {"type": "result", "subtype": "success"}),
            ("codex", {"type": "turn.completed"}),
            ("custom-jsonl", {"type": "agent.finished"}),
        ]:
            with self.subTest(adapter=adapter):
                self.assertEqual(parse(adapter, [terminal], None)[0]["error_code"], "process_incomplete")
                self.assertEqual(parse(adapter, [terminal], 2)[0]["error_code"], "process_exit")
                self.assertIsNone(parse(adapter, [terminal])[0]["error_code"])
                self.assertEqual(parse(adapter, ["bad-json"])[0]["error_code"], "missing_completion")
                self.assertEqual(parse(adapter, [])[0]["error_code"], "missing_completion")

    def test_pi_retry_end_is_not_terminal_and_usage_is_message_authoritative(self):
        objects = [
            {
                "type": "message_update",
                "usage": {"input": 999},
                "assistantMessageEvent": {"type": "text_delta", "delta": "hi"},
            },
            {
                "type": "message_end",
                "message": {
                    "id": "m",
                    "role": "assistant",
                    "model": "route",
                    "provider": "provider",
                    "content": [],
                    "usage": {"input": 3, "output": 0},
                },
            },
            {"type": "agent_end", "willRetry": True},
            {"type": "auto_retry_start", "attempt": 1},
        ]
        result, _ = parse("pi", objects)
        self.assertEqual(result["error_code"], "missing_completion")
        self.assertEqual(result["input_tokens"], 3)
        self.assertEqual(result["output_tokens"], 0)
        self.assertEqual(result["primary_observed_model"], "route")
        self.assertIsNone(result["cost_usd"])

    def test_provider_errors_and_permission_denials(self):
        cases = [
            (
                "pi",
                [
                    {
                        "type": "message_end",
                        "message": {
                            "role": "assistant",
                            "stopReason": "error",
                            "errorMessage": "Synthetic failure",
                        },
                    },
                    {"type": "agent_end"},
                ],
                "provider_error",
            ),
            (
                "claude",
                [
                    {
                        "type": "result",
                        "subtype": "error_during_execution",
                        "is_error": True,
                        "errors": ["Synthetic failure"],
                    }
                ],
                "provider_error",
            ),
            (
                "claude",
                [
                    {
                        "type": "result",
                        "subtype": "error_during_execution",
                        "is_error": True,
                        "permission_denials": [{"tool_name": "Bash"}],
                    }
                ],
                "permission_denied",
            ),
            ("codex", [{"type": "turn.failed", "error": {"message": "Synthetic failure"}}], "provider_error"),
        ]
        for adapter, objects, code in cases:
            with self.subTest(adapter=adapter, code=code):
                self.assertEqual(parse(adapter, objects)[0]["error_code"], code)

    def test_claude_success_after_optional_permission_denial_remains_gradeable(self):
        result, events = parse(
            "claude",
            [
                {
                    "type": "user",
                    "message": {
                        "content": [{"type": "tool_result", "tool_use_id": "optional", "is_error": True}]
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "Fixed and tested",
                    "permission_denials": [{"tool_name": "optional_plugin"}],
                },
            ],
        )
        self.assertIsNone(result["error_code"])
        self.assertEqual(result["final_response"], "Fixed and tested")
        self.assertTrue(any("permissions were denied" in warning for warning in result["parser_warnings"]))
        self.assertTrue(any(event["type"] == "warning" for event in events))

    def test_absent_usage_is_unknown_and_invalid_values_never_become_metrics(self):
        result, _ = parse(
            "codex",
            [
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": -1, "output_tokens": True, "cached_input_tokens": "123"},
                }
            ],
        )
        for key in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens", "cost_usd"):
            self.assertIsNone(result[key])
        self.assertEqual(result["configured_model"], "configured-alias")
        self.assertIsNone(result["primary_observed_model"])

    def test_claude_authoritative_cumulative_usage_and_model_usage(self):
        result, _ = parse(
            "claude",
            [
                {
                    "type": "assistant",
                    "message": {
                        "id": "m",
                        "model": "one-model",
                        "content": [],
                        "usage": {"input_tokens": 10, "output_tokens": 1},
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "id": "m",
                        "model": "one-model",
                        "content": [],
                        "usage": {"input_tokens": 10, "output_tokens": 4},
                    },
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "modelUsage": {
                        "one-model": {
                            "inputTokens": 10,
                            "outputTokens": 4,
                            "cacheReadInputTokens": 2,
                            "cacheCreationInputTokens": 0,
                            "costUSD": 0,
                        }
                    },
                },
            ],
        )
        self.assertEqual(result["input_tokens"], 10)
        self.assertEqual(result["output_tokens"], 4)
        self.assertEqual(result["turns"], 1)
        self.assertEqual(len(result["routes"]), 1)
        self.assertEqual(result["primary_observed_model"], "one-model")
        self.assertEqual(result["cost_usd"], 0)

    def test_bad_shapes_and_unknown_events_do_not_destroy_stream(self):
        for adapter, terminal in [
            ("pi", {"type": "agent_end"}),
            ("claude", {"type": "result", "subtype": "success"}),
            ("codex", {"type": "turn.completed"}),
        ]:
            result, events = parse(
                adapter,
                [
                    "[]",
                    '{"type":123}',
                    '{"type":"unexpected","secret":"DO_NOT_DISPLAY"}',
                    '{"type":"message_end","message":null}',
                    terminal,
                ],
            )
            self.assertIsNone(result["error_code"])
            self.assertGreaterEqual(len(result["parser_warnings"]), 3)
            self.assertNotIn("DO_NOT_DISPLAY", json.dumps(events))

    def test_new_turn_invalidates_previous_completion(self):
        for adapter, terminal, start in [
            ("pi", "agent_end", "turn_start"),
            ("codex", "turn.completed", "turn.started"),
            ("custom-jsonl", "agent.finished", "turn.started"),
        ]:
            self.assertEqual(
                parse(adapter, [{"type": terminal}, {"type": start}])[0]["error_code"], "missing_completion"
            )

    def test_malformed_claude_result_is_not_a_valid_terminal_event(self):
        for result in ({"type": "result"}, {"type": "result", "subtype": "success", "modelUsage": ["bad"]}):
            parsed, _ = parse("claude", [result])
            self.assertEqual(parsed["error_code"], "missing_completion")
            self.assertTrue(parsed["parser_warnings"])

    def test_custom_normalized_protocol(self):
        result, _ = parse(
            "custom-jsonl",
            [
                {"type": "turn.started"},
                {"type": "route.observed", "route": {"provider": "test", "model": "observed"}},
                {"type": "usage.updated", "usage": {"input_tokens": 3, "output_tokens": 2, "cost_usd": 0.01}},
                {"type": "tool.started", "tool": {"name": "test", "call_id": "t"}},
                {"type": "tool.finished", "tool": {"name": "test", "call_id": "t"}},
                {"type": "agent.finished", "text": "Done"},
            ],
        )
        self.assertIsNone(result["error_code"])
        self.assertEqual(result["tool_calls"], 1)
        self.assertEqual(result["primary_observed_model"], "observed")
        self.assertEqual(result["final_response"], "Done")


class CommandTests(unittest.TestCase):
    def test_defaults_are_argument_arrays_with_unattended_permissions(self):
        prompt = "--dangerously-skip-permissions ; $(echo bad) `echo bad`"
        for adapter in ("pi", "claude", "codex"):
            argv = build_command(
                {"adapter": adapter, "model": "Auto-3-API"}, prompt, Path("/tmp/path with spaces"), "attempt"
            )
            self.assertEqual(argv[-2:], ["--", prompt])
            self.assertIn("Auto-3-API", argv)
        codex = build_command({"adapter": "codex", "model": "alias"}, "test", Path("/tmp/ws"), "a")
        self.assertEqual(codex[:5], ["codex", "--no-daemon", "-a", "never", "exec"])

    def test_custom_placeholder_expansion_and_unknown_fields(self):
        profile = {
            "adapter": "custom-jsonl",
            "model": "alias",
            "command": ["agent", "{model}", "{workspace}", "{attempt_id}", "{prompt}"],
        }
        self.assertEqual(
            build_command(profile, "hello; whoami", Path("/tmp/ws"), "a"),
            ["agent", "alias", "/tmp/ws", "a", "hello; whoami"],
        )
        for placeholder in ("{unknown}", "{model.__class__}", "{model!r}", "{model:>4}"):
            with self.subTest(placeholder=placeholder), self.assertRaises(ValueError):
                build_command({**profile, "command": ["agent", placeholder]}, "p", Path("/tmp/ws"), "a")

    def test_unsafe_overrides_rejected(self):
        for command in (
            ["sh", "-c", "{prompt}"],
            ["agent", "--api-key", "secret"],
            ["agent", "--yolo"],
            ["agent", "--sandbox", "danger-full-access"],
            ["agent", "--sandbox", "workspace-write", "-s", "danger-full-access"],
            ["env", "API_KEY=secret", "agent"],
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                build_command(
                    {"adapter": "custom-jsonl", "model": "alias", "command": command},
                    "p",
                    Path("/tmp/ws"),
                    "a",
                )
        for adapter in ("pi", "claude", "codex"):
            with self.assertRaises(ValueError):
                build_command(
                    {"adapter": adapter, "model": "alias", "command": [adapter, "{prompt}"]},
                    "p",
                    Path("/tmp/ws"),
                    "a",
                )

    def test_environment_preserves_home_without_ambient_secrets(self):
        with patch.dict(
            os.environ,
            {
                "PATH": "/bin",
                "HOME": "/synthetic/home",
                "API_KEY": "synthetic-secret",
                "LC_API_KEY": "synthetic-secret",
                "SSH_AUTH_SOCK": "/synthetic/socket",
                "LANG": "C",
                "LC_ALL": "C",
            },
            clear=True,
        ):
            env = _environment({})
            self.assertEqual(env, {"PATH": "/bin", "HOME": "/synthetic/home", "LANG": "C", "LC_ALL": "C"})
            self.assertEqual(_environment({"env_allowlist": ["API_KEY"]})["API_KEY"], "synthetic-secret")

    def test_preflight_only_calls_help_and_version_and_never_reports_stdout(self):
        help_text = (
            "--no-daemon --ask-for-approval never --ephemeral --json --sandbox workspace-write --model"
        )
        run = AsyncMock(
            side_effect=[(0, "codex-cli 0.156.1 synthetic-secret"), (0, help_text), (0, help_text)]
        )
        with (
            patch("routebench.adapters.shutil.which", return_value="/synthetic/bin/codex"),
            patch("routebench.adapters._help_command", run),
        ):
            result = asyncio.run(preflight({"id": "codex", "adapter": "codex", "model": "alias"}))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["version"], "0.156.1")
        self.assertEqual(result["authentication"], "not_checked")
        self.assertNotIn("synthetic-secret", json.dumps(result))
        self.assertEqual(
            [call.args[0][1:] for call in run.call_args_list], [["--version"], ["--help"], ["exec", "--help"]]
        )

    def test_preflight_missing_flags_and_executable_block(self):
        profile = {"id": "pi", "adapter": "pi", "model": "Auto-3-API"}
        with patch("routebench.adapters.shutil.which", return_value=None):
            self.assertEqual(asyncio.run(preflight(profile))["status"], "missing")
        with (
            patch("routebench.adapters.shutil.which", return_value="/synthetic/pi"),
            patch(
                "routebench.adapters._help_command", AsyncMock(side_effect=[(0, "0.1.0"), (0, "old help")])
            ),
        ):
            self.assertEqual(asyncio.run(preflight(profile))["status"], "blocked")
        with (
            patch("routebench.adapters.shutil.which", return_value="/synthetic/pi"),
            patch(
                "routebench.adapters._help_command",
                AsyncMock(side_effect=[(1, "0.1.0"), (0, "--mode json --no-session --model")]),
            ),
        ):
            self.assertEqual(asyncio.run(preflight(profile))["status"], "blocked")


class ActualCaptureTests(unittest.TestCase):
    def test_actual_pi_routes_and_completed_message_usage(self):
        result, events = parse(
            "pi", (FIXTURES / "pi-0.84.4-Auto-3-API.actual.jsonl").read_text().splitlines()
        )
        self.assertIsNone(result["error_code"])
        self.assertEqual(result["primary_observed_model"], "gpt-5.6-sol")
        self.assertEqual(len(result["routes"]), 5)
        self.assertTrue(
            all(
                route["model"] == "Auto-3-API" and route["response_model"] == "gpt-5.6-sol"
                for route in result["routes"]
            )
        )
        self.assertEqual(result["input_tokens"], 15)
        self.assertEqual(result["output_tokens"], 818)
        self.assertEqual(result["cached_input_tokens"], 31648)
        self.assertEqual(result["cache_write_tokens"], 8655)
        self.assertEqual(result["reasoning_tokens"], 165)
        self.assertEqual(result["cost_usd"], 0)
        self.assertEqual(result["tool_calls"], 11)
        self.assertEqual(result["turns"], 5)
        self.assertEqual(sum(event["type"] == "usage.updated" for event in events), 5)

    def test_actual_claude_success_uses_terminal_cumulative_usage(self):
        result, _ = parse(
            "claude", (FIXTURES / "claude-2.1.280-sonnet.actual.jsonl").read_text().splitlines()
        )
        self.assertIsNone(result["error_code"])
        self.assertEqual(result["primary_observed_model"], "claude-sonnet-5")
        self.assertEqual(len(result["routes"]), 14)
        self.assertEqual(result["turns"], 14)
        self.assertEqual(result["tool_calls"], 13)
        self.assertEqual(result["input_tokens"], 28)
        self.assertEqual(result["output_tokens"], 6112)
        self.assertEqual(result["cached_input_tokens"], 1413629)
        self.assertEqual(result["cache_write_tokens"], 110996)
        self.assertEqual(result["reasoning_tokens"], 3646)
        self.assertAlmostEqual(result["cost_usd"], 0.6213918)
        self.assertIn("average_amount", result["final_response"])

    def test_actual_claude_errors_do_not_invent_synthetic_routes(self):
        for alias, rejected in (("opus", "claude-opus-5-5"), ("fable", "claude-fable-5-1")):
            with self.subTest(alias=alias):
                result, events = parse(
                    "claude",
                    (FIXTURES / f"claude-2.1.280-{alias}-error.actual.jsonl").read_text().splitlines(),
                    1,
                )
                self.assertEqual(result["error_code"], "provider_error")
                self.assertIn(rejected, result["error_message"])
                self.assertEqual(result["routes"], [])
                self.assertIsNone(result["primary_observed_model"])
                self.assertFalse(any(event["type"] == "route.observed" for event in events))
                self.assertEqual(result["input_tokens"], 0)
                self.assertEqual(result["reasoning_tokens"], 0)

    def test_actual_codex_alias_errors_remain_terminal_failures(self):
        for alias in ("sol", "terra"):
            with self.subTest(alias=alias):
                result, events = parse(
                    "codex",
                    (FIXTURES / f"codex-0.156.1-{alias}-error.actual.jsonl").read_text().splitlines(),
                    1,
                )
                self.assertEqual(result["error_code"], "provider_error")
                self.assertIn(f"'{alias}' model is not supported", result["error_message"])
                self.assertEqual(result["routes"], [])
                self.assertIsNone(result["cost_usd"])
                self.assertIsNone(result["input_tokens"])
                self.assertTrue(any(event["type"] == "warning" for event in events))

    def test_codex_diagnostic_item_does_not_fail_a_successful_turn(self):
        # This composition is synthetic: the observed diagnostic followed by a synthetic success.
        source = (FIXTURES / "codex-0.156.1-sol-error.actual.jsonl").read_text().splitlines()
        warning = next(json.loads(line) for line in source if json.loads(line)["type"] == "item.completed")
        result, events = parse("codex", [warning, {"type": "turn.started"}, {"type": "turn.completed"}])
        self.assertIsNone(result["error_code"])
        self.assertEqual(events[0]["type"], "warning")

    def test_actual_fixture_provenance_and_removed_sensitive_payloads(self):
        provenance = json.loads((FIXTURES / "actual-provenance.json").read_text())
        self.assertEqual(provenance["capture_date"], "2026-09-23")
        self.assertEqual(len(provenance["captures"]), 6)
        for capture in provenance["captures"]:
            with self.subTest(fixture=capture["fixture"]):
                self.assertIn("actual CLI stdout", capture["origin"])
                self.assertEqual(len(capture["source_sha256"]), 64)
                self.assertTrue(capture["source_path"].startswith(".routebench/artifacts/"))
                text = (FIXTURES / capture["fixture"]).read_text()
                self.assertNotIn("/Users/", text)
                self.assertNotIn("__hidden__", text)
                for line in text.splitlines():
                    event = json.loads(line)
                    messages = [event.get("message", {}), *event.get("messages", [])]
                    for message in messages:
                        if not isinstance(message, dict):
                            continue
                        for part in message.get("content", []):
                            self.assertNotIn(part["type"], {"thinking", "reasoning"})
                            if part["type"] in {"toolCall", "tool_use", "tool_result"}:
                                self.assertFalse({"args", "arguments", "input", "content"} & part.keys())


if __name__ == "__main__":
    unittest.main()
