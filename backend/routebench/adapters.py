"""CLI invocation and incremental JSONL normalization; no model calls at import time."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import signal
import string
import tempfile
from pathlib import Path

ADAPTERS = {"pi", "claude", "codex", "custom-jsonl"}
TOKEN_FIELDS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)
MAX_LINE = 2 * 1024 * 1024
MAX_TEXT = 200_000
MAX_RECORDS = 10_000


def _environment(profile: dict) -> dict[str, str]:
    allowed = {
        "PATH",
        "HOME",
        "LANG",
        "TMPDIR",
        "TMP",
        "TEMP",
        "LC_ALL",
        "LC_CTYPE",
        "LC_COLLATE",
        "LC_MESSAGES",
        "LC_NUMERIC",
        "LC_TIME",
        "LC_MONETARY",
        *profile.get("env_allowlist", []),
    }
    return {key: value for key, value in os.environ.items() if key in allowed}


def _option(argv: list[str], *names: str) -> str | None:
    for index, arg in enumerate(argv):
        if arg == "--":
            break
        for name in names:
            if arg == name:
                return argv[index + 1] if index + 1 < len(argv) else ""
            if arg.startswith(name + "="):
                return arg.split("=", 1)[1]
    return None


def build_command(profile: dict, prompt: str, workspace: Path, attempt_id: str) -> list[str]:
    """Expand only documented placeholders, keeping every argument out of a shell."""
    adapter = profile.get("adapter")
    if adapter not in ADAPTERS:
        raise ValueError("Unsupported adapter")
    model = profile.get("model", "")
    if not isinstance(model, str) or not model or "\0" in model:
        raise ValueError("A nonempty model is required")
    defaults = {
        "pi": ["pi", "--mode", "json", "--no-session", "--model", "{model}", "--", "{prompt}"],
        "claude": [
            "claude",
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--model",
            "{model}",
            "--permission-mode",
            "acceptEdits",
            "--permission-prompts",
            "none",
            "--no-session-persistence",
            "--allowedTools",
            "Read,Edit,Write,Glob,Grep,Bash(python:*),Bash(python3:*),Bash(pytest:*),Bash(git diff:*),Bash(git status:*)",
            "--",
            "{prompt}",
        ],
        "codex": [
            "codex",
            "--no-daemon",
            "-a",
            "never",
            "exec",
            "--ephemeral",
            "--json",
            "--sandbox",
            "workspace-write",
            "--model",
            "{model}",
            "--",
            "{prompt}",
        ],
    }
    template = profile.get("command") or defaults.get(adapter)
    if (
        not isinstance(template, list)
        or not template
        or not all(isinstance(arg, str) and arg and "\0" not in arg for arg in template)
    ):
        raise ValueError("Command must be a nonempty argument array")
    values = {"model": model, "prompt": prompt, "workspace": str(workspace), "attempt_id": attempt_id}
    argv = []
    for index, arg in enumerate(template):
        for _, field, spec, conversion in string.Formatter().parse(arg):
            if field is not None and (index == 0 or field not in values or spec or conversion):
                raise ValueError("Command contains an unsupported placeholder")
        expanded = arg.format_map(values)
        if "\0" in expanded:
            raise ValueError("Command arguments cannot contain null bytes")
        argv.append(expanded)
    # Inspect template flags, not prompt text, so a quoted prompt cannot become a flag.
    flags = template[: template.index("--")] if "--" in template else template
    if Path(argv[0]).name.lower() in {"sh", "bash", "zsh", "fish", "cmd", "cmd.exe", "powershell", "pwsh"}:
        raise ValueError("Shell command wrappers are not allowed")
    for arg in flags:
        if arg.startswith("--") and re.search(
            r"(?:api[-_]?key|access[-_]?token|bearer|password|credential|secret|dangerously|^--yolo$)",
            arg,
            re.I,
        ):
            raise ValueError("Credentials and permission bypass flags are not allowed in command arguments")
        if re.match(r"(?:[A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)[A-Z0-9_]*)=", arg):
            raise ValueError("Secret environment assignments are not allowed in command arguments")
    if any("danger-full-access" in arg for arg in flags):
        raise ValueError("Unrestricted sandbox mode is not allowed")
    required = {
        "pi": [("--mode", "json"), ("--no-session", None)],
        "claude": [
            ("--output-format", "stream-json"),
            ("--verbose", None),
            ("--permission-mode", "acceptEdits"),
            ("--permission-prompts", "none"),
            ("--no-session-persistence", None),
        ],
        "codex": [
            ("--no-daemon", None),
            ("exec", None),
            ("--ephemeral", None),
            ("--json", None),
            ("--sandbox", "workspace-write"),
        ],
    }
    for flag, value in required.get(adapter, []):
        if (value is None and flag not in flags) or (value is not None and _option(flags, flag) != value):
            raise ValueError(
                "Command override must preserve unattended structured output and workspace permissions"
            )
        if sum(arg == flag or arg.startswith(flag + "=") for arg in flags) > 1:
            raise ValueError("Protected command options must not be repeated")
    if adapter == "claude" and not ({"-p", "--print"} & set(flags)):
        raise ValueError("Claude commands must use print mode")
    if adapter == "codex" and _option(flags, "-a", "--ask-for-approval") != "never":
        raise ValueError("Codex commands must disable interactive approval prompts")
    if (
        adapter == "codex"
        and sum(
            arg in {"-a", "--ask-for-approval"} or arg.startswith(("-a=", "--ask-for-approval="))
            for arg in flags
        )
        != 1
    ):
        raise ValueError("Approval policy must be specified exactly once")
    return argv


async def _help_command(argv: list[str], env: dict, cwd: str) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )

    async def consume() -> str:
        result = bytearray()
        while chunk := await process.stdout.read(8192):
            result.extend(chunk[: max(0, 131072 - len(result))])
        await process.wait()
        return result.decode("utf-8", errors="replace")

    try:
        output = await asyncio.wait_for(consume(), 10)
        return process.returncode, output
    except BaseException:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
        raise


async def preflight(profile: dict) -> dict:
    """Check installed flags using version/help only. Saved authentication is opaque."""
    adapter = profile.get("adapter")
    result = {
        "profile_id": profile.get("id"),
        "status": "blocked",
        "executable": None,
        "resolved_path": None,
        "version": None,
        "structured_output": False,
        "authentication": "not_checked",
        "warnings": [],
        "capabilities": {
            "usage": adapter in ADAPTERS,
            "reported_cost": adapter in {"pi", "claude", "custom-jsonl"},
            "route": adapter in {"pi", "claude", "custom-jsonl"},
            "tool_events": adapter in ADAPTERS,
            "file_events": adapter == "codex",
        },
    }
    try:
        command = build_command(
            profile, "preflight-placeholder", Path("/tmp/routebench-preflight"), "preflight"
        )
    except ValueError as error:
        result["warnings"].append(str(error))
        return result
    result["executable"] = command[0]
    env = _environment(profile)
    executable = shutil.which(command[0], path=env.get("PATH", os.defpath))
    result["resolved_path"] = executable
    if not executable:
        result.update(status="missing", warnings=["CLI executable was not found on PATH"])
        return result
    try:
        with tempfile.TemporaryDirectory(prefix="routebench-preflight-") as cwd:
            version_code, version_text = await _help_command([executable, "--version"], env, cwd)
            match = re.search(r"\b\d+\.\d+\.\d+(?:[-+][a-zA-Z0-9.-]+)?\b", version_text)
            result["version"] = match.group(0) if match else None
            help_code, help_text = await _help_command([executable, "--help"], env, cwd)
            if adapter == "codex":
                exec_code, exec_help = await _help_command([executable, "exec", "--help"], env, cwd)
                help_code = help_code or exec_code
                help_text += "\n" + exec_help
        required = {
            "pi": ["--mode", "json", "--no-session", "--model"],
            "claude": [
                "--print",
                "--output-format",
                "stream-json",
                "--verbose",
                "--model",
                "--permission-mode",
                "acceptEdits",
                "--permission-prompts",
                "none",
                "--no-session-persistence",
                "--allowedTools",
            ],
            "codex": [
                "--no-daemon",
                "--ask-for-approval",
                "never",
                "--ephemeral",
                "--json",
                "--sandbox",
                "workspace-write",
                "--model",
            ],
        }.get(adapter, [])
        missing = [flag for flag in required if flag not in help_text]
        if version_code or help_code:
            result["warnings"].append("CLI version/help returned a nonzero exit code")
        elif missing:
            result["warnings"].append(
                "Installed CLI help does not advertise required options: " + ", ".join(missing)
            )
        elif not help_text.strip():
            result["warnings"].append("CLI did not return readable help")
        else:
            result.update(status="ready", structured_output=True)
            if adapter == "custom-jsonl":
                result["warnings"].append("Custom JSONL protocol is user-declared and has not been exercised")
        result["warnings"].append("Authentication was not checked; existing CLI login and HOME are reused")
        if profile.get("command"):
            result["warnings"].append(
                "Command override is active; help checks cannot establish runtime compatibility"
            )
        if profile.get("env_allowlist"):
            result["warnings"].append(
                "Explicitly allowed environment variables are accessible to agent tools"
            )
    except (OSError, asyncio.TimeoutError):
        result["warnings"].append("CLI version/help preflight failed or exceeded 10 seconds")
    return result


def _number(value):
    return (
        value
        if isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
        else None
    )


def _usage(value: dict | None) -> dict:
    value = value if isinstance(value, dict) else {}
    aliases = {
        "input_tokens": ("input_tokens", "inputTokens", "input"),
        "output_tokens": ("output_tokens", "outputTokens", "output"),
        "cached_input_tokens": (
            "cached_input_tokens",
            "cache_read_input_tokens",
            "cacheReadInputTokens",
            "cacheRead",
        ),
        "cache_write_tokens": (
            "cache_write_tokens",
            "cache_write_input_tokens",
            "cache_creation_input_tokens",
            "cacheCreationInputTokens",
            "cacheWrite",
        ),
        "reasoning_tokens": ("reasoning_tokens", "reasoning_output_tokens", "reasoningTokens", "reasoning"),
    }
    result = {
        field: next((_number(value[key]) for key in names if _number(value.get(key)) is not None), None)
        for field, names in aliases.items()
    }
    detail = value.get("output_tokens_details") or {}
    if isinstance(detail, dict) and result["reasoning_tokens"] is None:
        result["reasoning_tokens"] = _number(detail.get("reasoning_tokens", detail.get("thinking_tokens")))
    cost = value.get("cost")
    result["cost_usd"] = (
        _number(cost.get("total"))
        if isinstance(cost, dict)
        else _number(value.get("cost_usd", value.get("costUSD", cost)))
    )
    return result


def _text(content) -> str:
    if isinstance(content, str):
        return content[:MAX_TEXT]
    if isinstance(content, list):
        return "\n".join(
            part.get("text", "")
            for part in content
            if isinstance(part, dict)
            and part.get("type") in {"text", "output_text"}
            and isinstance(part.get("text"), str)
        )[:MAX_TEXT]
    return ""


class AdapterParser:
    """Bounded parser state. Raw artifact storage and credential redaction belong to the runner."""

    def __init__(self, adapter: str, configured_model: str):
        if adapter not in ADAPTERS:
            raise ValueError("Unsupported adapter")
        self.adapter = adapter
        self.configured_model = configured_model
        self.line_number = 0
        self.turn_index = 0
        self.retries = 0
        self.final_response = ""
        self.routes = []
        self.parser_warnings = []
        self.error_code = None
        self.error_message = None
        self._complete = False
        self._usage_records = {}
        self._final_usage = None
        self._route_keys = set()
        self._tool_ids = {}
        self._messages = set()
        self._retry_ids = set()
        self._terminal_turns = set()
        self._codex_usage_complete = True

    def _event(self, kind: str, raw: str, **data) -> dict:
        return {
            "type": kind,
            "source": self.adapter,
            "turn_index": self.turn_index,
            "raw_event_type": raw,
            **data,
        }

    def _warn(self, message: str, raw: str = "") -> dict:
        warning = f"Line {self.line_number}: {message}"
        if len(self.parser_warnings) < 100:
            self.parser_warnings.append(warning)
        return self._event("warning", raw, text=warning)

    def _error(self, message: str, code: str = "provider_error", raw: str = "error") -> dict:
        self.error_code, self.error_message = code, str(message)[:4000]
        return self._event("error", raw, text=self.error_message)

    def _key(self, obj: dict) -> str:
        return str(
            obj.get("id")
            or obj.get("responseId")
            or obj.get("uuid")
            or hashlib.sha256(json.dumps(obj, sort_keys=True).encode()).hexdigest()
        )

    def _record_usage(self, key: str, value: dict, raw: str) -> dict:
        normalized = _usage(value)
        if len(self._usage_records) < MAX_RECORDS or key in self._usage_records:
            self._usage_records[key] = normalized
        else:
            return self._warn("Usage record limit reached", raw)
        return self._event("usage.updated", raw, usage=normalized)

    def _route(self, key: str, model, provider, raw: str, usage=None, response_model=None) -> list[dict]:
        if not isinstance(model, str) or not model or key in self._route_keys:
            return []
        if model.strip().lower() in {"unknown", "n/a", "none", "null"} or re.fullmatch(
            r"<[^>]+>", model.strip()
        ):
            return []
        if len(self.routes) >= MAX_RECORDS:
            return [self._warn("Route record limit reached", raw)]
        route = {
            "turn_index": self.turn_index,
            "provider": provider if isinstance(provider, str) else None,
            "model": model,
        }
        if isinstance(response_model, str) and response_model:
            route["response_model"] = response_model
        if usage is not None:
            route["usage"] = _usage(usage)
            route["cost_usd"] = route["usage"]["cost_usd"]
        self.routes.append(route)
        self._route_keys.add(key)
        return [self._event("route.observed", raw, route=route)]

    def _tool(self, call_id, name, status: str, raw: str, summary="") -> dict:
        key = str(call_id) if call_id is not None else f"{self.turn_index}:{name}"
        name = str(name or self._tool_ids.get(key) or "unknown")
        if len(self._tool_ids) < MAX_RECORDS:
            self._tool_ids[key] = name
        return self._event(
            "tool." + status,
            raw,
            tool={
                "name": name,
                "call_id": key,
                "status": {
                    "started": "running",
                    "updated": "running",
                    "finished": "succeeded",
                    "failed": "failed",
                }.get(status, status),
                "summary": str(summary)[:2000],
            },
        )

    def feed(self, line: str) -> list[dict]:
        self.line_number += 1
        if len(line) > MAX_LINE:
            return [self._warn("Oversized JSONL record skipped")]
        if not line.strip():
            return []
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, RecursionError):
            return [self._warn("Malformed JSON or diagnostic stdout")]
        if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
            return [self._warn("JSON record has no string event type")]
        try:
            return getattr(self, "_" + self.adapter.replace("-", "_"))(obj)
        except (TypeError, ValueError, KeyError, AttributeError, RecursionError):
            return [self._warn("Malformed event fields skipped", obj["type"])]

    def _pi_message(self, message: dict, raw: str) -> list[dict]:
        if message.get("role") != "assistant":
            return []
        key = self._key(message)
        if key in self._messages:
            return []
        if len(self._messages) >= MAX_RECORDS:
            return [self._warn("Message record limit reached", raw)]
        self._messages.add(key)
        self.turn_index = max(1, self.turn_index)
        events = []
        if isinstance(message.get("usage"), dict):
            events.append(self._record_usage(key, message["usage"], raw))
        events += self._route(
            key,
            message.get("model"),
            message.get("provider"),
            raw,
            message.get("usage"),
            message.get("responseModel"),
        )
        text = _text(message.get("content"))
        if text:
            self.final_response = text
            events.append(self._event("message.finished", raw, text=text))
        if message.get("stopReason") in {"error", "aborted"}:
            events.append(self._error(message.get("errorMessage") or "Assistant message failed", raw=raw))
        return events

    def _pi(self, obj: dict) -> list[dict]:
        raw = obj["type"]
        if raw in {"session", "agent_start"}:
            self._complete = False
            return [self._event("session.started", raw)]
        if raw == "turn_start":
            self.turn_index += 1
            self._complete = False
            return [self._event("turn.started", raw)]
        if raw == "message_start":
            return (
                [self._event("message.started", raw)]
                if obj.get("message", {}).get("role") == "assistant"
                else []
            )
        if raw in {"message_end", "turn_end"}:
            events = self._pi_message(obj.get("message", {}), raw)
            if raw == "turn_end":
                events.append(self._event("turn.finished", raw))
            return events
        if raw == "message_update":
            update = obj.get("assistantMessageEvent", {})
            return (
                [self._event("message.delta", raw, text=str(update.get("delta", ""))[:MAX_TEXT])]
                if update.get("type") == "text_delta"
                else []
            )
        if raw.startswith("tool_execution_"):
            status = {"start": "started", "update": "updated", "end": "finished"}.get(raw.rsplit("_", 1)[1])
            if status:
                args = obj.get("args") or {}
                event = self._tool(
                    obj.get("toolCallId"),
                    obj.get("toolName"),
                    status,
                    raw,
                    args.get("command") or args.get("path") or "",
                )
                if obj.get("isError"):
                    event["tool"]["status"] = "failed"
                return [event]
        if raw in {"auto_retry_start", "retry_start", "summarization_retry_scheduled"}:
            key = (self.turn_index, raw, obj.get("attempt"))
            if key not in self._retry_ids:
                self.retries += 1
                if len(self._retry_ids) < MAX_RECORDS:
                    self._retry_ids.add(key)
            self._complete = False
            return [self._event("retry.started", raw)]
        if raw in {"auto_retry_end", "retry_end"}:
            events = [self._event("retry.finished", raw)]
            if obj.get("success"):
                self.error_code = self.error_message = None
            elif obj.get("finalError"):
                events.append(self._error(obj["finalError"], raw=raw))
            return events
        if raw in {"agent_end", "agent_settled"}:
            events = []
            for message in obj.get("messages", []):
                events += self._pi_message(message, raw)
            self._complete = not obj.get("willRetry", False)
            if self._complete:
                events.append(self._event("agent.finished", raw))
            return events
        if raw == "error":
            return [self._error(obj.get("message") or obj.get("error") or "Agent error", raw=raw)]
        # Supplementary router entries remain in raw artifacts; never mistake configured aliases for observed routes.
        return [self._warn("Unrecognized event retained in raw artifact", raw)]

    def _claude(self, obj: dict) -> list[dict]:
        raw = obj["type"]
        if raw == "system":
            if obj.get("subtype") == "init":
                return [self._event("session.started", raw)]
            if obj.get("subtype") in {"api_retry", "retry"}:
                self.retries += 1
                return [self._event("retry.started", raw)]
            return []
        if raw == "assistant":
            message = obj.get("message", {})
            key = self._key(message)
            if key not in self._messages:
                if len(self._messages) >= MAX_RECORDS:
                    return [self._warn("Message record limit reached", raw)]
                self._messages.add(key)
                self.turn_index += 1
            self._complete = False
            events = []
            if isinstance(message.get("usage"), dict):
                events.append(self._record_usage(key, message["usage"], raw))
            events += self._route(key, message.get("model"), None, raw, message.get("usage"))
            text = _text(message.get("content"))
            if text:
                self.final_response = text
                events.append(self._event("message.finished", raw, text=text))
            for part in message.get("content", []):
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    args = part.get("input") or {}
                    events.append(
                        self._tool(
                            part.get("id"),
                            part.get("name"),
                            "started",
                            raw,
                            args.get("command") or args.get("file_path") or "",
                        )
                    )
            if obj.get("error"):
                events.append(self._error(str(obj["error"]), raw=raw))
            return events
        if raw == "user":
            events = []
            for part in obj.get("message", {}).get("content", []):
                if isinstance(part, dict) and part.get("type") == "tool_result":
                    event = self._tool(part.get("tool_use_id"), part.get("name"), "finished", raw)
                    if part.get("is_error"):
                        event["tool"]["status"] = "failed"
                    events.append(event)
            return events
        if raw == "stream_event":
            event = obj.get("event", {})
            delta = event.get("delta", {})
            if event.get("type") == "content_block_delta" and delta.get("type") == "text_delta":
                return [self._event("message.delta", raw, text=str(delta.get("text", ""))[:MAX_TEXT])]
            return []
        if raw == "result":
            if obj.get("subtype") not in {
                "success",
                "error_max_turns",
                "error_during_execution",
                "error_max_budget_usd",
                "error_max_structured_output_retries",
            } and not obj.get("is_error"):
                return [self._warn("Result is missing a recognized terminal subtype", raw)]
            if isinstance(obj.get("result"), str):
                self.final_response = obj["result"][:MAX_TEXT]
            if isinstance(obj.get("num_turns"), int) and obj["num_turns"] >= 0:
                self.turn_index = obj["num_turns"]
            model_usage = obj.get("modelUsage") or obj.get("model_usage") or {}
            events = []
            for model, usage in model_usage.items():
                if not any(route["model"] == model for route in self.routes):
                    events += self._route("result:" + model, model, None, raw, usage)
            if isinstance(obj.get("usage"), dict):
                self._final_usage = _usage(obj["usage"])
            elif model_usage:
                self._final_usage = self._sum([_usage(item) for item in model_usage.values()])
            if _number(obj.get("total_cost_usd")) is not None:
                self._final_usage = self._final_usage or self._sum(list(self._usage_records.values()))
                self._final_usage["cost_usd"] = obj["total_cost_usd"]
            if self._final_usage:
                events.append(self._event("usage.updated", raw, usage=self._final_usage))
            if obj.get("is_error") or str(obj.get("subtype", "")).startswith("error"):
                errors = obj.get("errors") or [obj.get("result") or "Claude returned an error result"]
                events.append(
                    self._error(
                        "; ".join(map(str, errors)) if isinstance(errors, list) else str(errors), raw=raw
                    )
                )
            if obj.get("permission_denials"):
                if obj.get("subtype") == "success" and not obj.get("is_error"):
                    events.append(
                        self._warn("Tool permissions were denied, but Claude completed successfully", raw)
                    )
                else:
                    events.append(
                        self._error("Required tool permissions were denied", "permission_denied", raw)
                    )
            self._complete = True
            events.append(self._event("agent.finished", raw))
            return events
        if raw == "error":
            return [self._error(obj.get("message") or obj.get("error") or "Claude stream error", raw=raw)]
        return [self._warn("Unrecognized event retained in raw artifact", raw)]

    def _codex(self, obj: dict) -> list[dict]:
        raw = obj["type"]
        if raw == "thread.started":
            return [self._event("session.started", raw)]
        if raw == "turn.started":
            self.turn_index += 1
            self._complete = False
            return [self._event("turn.started", raw)]
        if raw in {"turn.completed", "turn.failed"}:
            self.turn_index = max(1, self.turn_index)
            self._complete = True
            key = str(obj.get("turn_id") or self.turn_index)
            events = []
            if key not in self._terminal_turns:
                if len(self._terminal_turns) < MAX_RECORDS:
                    self._terminal_turns.add(key)
                else:
                    self._codex_usage_complete = False
                usage = obj.get("usage")
                normalized = _usage(usage)
                required = ("input_tokens", "cached_input_tokens", "output_tokens")
                if any(normalized[field] is None for field in required):
                    self._codex_usage_complete = False
                elif normalized["cached_input_tokens"] > normalized["input_tokens"]:
                    self._codex_usage_complete = False
                if isinstance(usage, dict) and "cache_write_input_tokens" in usage and normalized["cache_write_tokens"] is None:
                    self._codex_usage_complete = False
                if isinstance(obj.get("usage"), dict):
                    events.append(self._record_usage(key, obj["usage"], raw))
            if raw == "turn.failed":
                error = obj.get("error", {})
                events.append(
                    self._error(
                        error.get("message", "Codex turn failed") if isinstance(error, dict) else str(error),
                        raw=raw,
                    )
                )
            events += [self._event("turn.finished", raw), self._event("agent.finished", raw)]
            return events
        if raw in {"item.started", "item.updated", "item.completed"}:
            item = obj.get("item", {})
            kind = item.get("type")
            if kind == "reasoning":
                return []
            if kind == "agent_message":
                text = _text(item.get("text"))
                if raw == "item.completed":
                    self.final_response = text
                    return [self._event("message.finished", raw, text=text)]
                return []
            if kind in {"command_execution", "mcp_tool_call", "web_search", "collab_tool_call"}:
                status = {"item.started": "started", "item.updated": "updated", "item.completed": "finished"}[
                    raw
                ]
                event = self._tool(
                    item.get("id"), item.get("tool") or kind, status, raw, item.get("command", "")
                )
                if item.get("status") == "failed" or item.get("exit_code") not in {None, 0}:
                    event["tool"]["status"] = "failed"
                return [event]
            if kind == "file_change":
                events = [
                    self._tool(
                        item.get("id"),
                        "file_change",
                        "finished" if raw == "item.completed" else "started",
                        raw,
                    )
                ]
                if raw == "item.completed":
                    events += [
                        self._event(
                            "file.changed", raw, file={"path": change.get("path"), "kind": change.get("kind")}
                        )
                        for change in item.get("changes", [])
                        if isinstance(change, dict)
                    ]
                return events
            if kind == "error":
                # Codex uses these items for nonfatal configuration diagnostics before a turn.
                return [self._warn(str(item.get("message", "Codex item diagnostic"))[:4000], raw)]
            return []
        if raw == "error":
            message = str(obj.get("message", "Codex stream error"))
            if re.search(r"reconnecting|retrying", message, re.I):
                self.retries += 1
                return [self._event("retry.started", raw)]
            return [self._error(message, raw=raw)]
        return [self._warn("Unrecognized event retained in raw artifact", raw)]

    def _custom_jsonl(self, obj: dict) -> list[dict]:
        raw = obj["type"]
        if raw == "turn.started":
            self.turn_index += 1
            self._complete = False
        if raw == "route.observed":
            route = obj.get("route", {})
            return self._route(
                self._key(obj),
                route.get("model"),
                route.get("provider"),
                raw,
                route.get("usage"),
                route.get("response_model"),
            )
        if raw == "usage.updated":
            self._final_usage = _usage(obj.get("usage"))
            return [self._event(raw, raw, usage=self._final_usage)]
        if raw.startswith("tool.") and raw.rsplit(".", 1)[1] in {"started", "updated", "finished"}:
            tool = obj.get("tool", {})
            return [
                self._tool(
                    tool.get("call_id"), tool.get("name"), raw.rsplit(".", 1)[1], raw, tool.get("summary", "")
                )
            ]
        if raw in {"message.finished", "agent.finished"} and isinstance(obj.get("text"), str):
            self.final_response = obj["text"][:MAX_TEXT]
        if raw == "agent.finished":
            self._complete = True
            if obj.get("error") or obj.get("status") in {"failed", "error"}:
                return [self._error(obj.get("error") or "Custom agent failed", raw=raw)]
        if raw == "error":
            return [self._error(obj.get("text") or "Custom agent error", raw=raw)]
        if raw == "retry.started":
            self.retries += 1
        if raw in {
            "session.started",
            "turn.started",
            "turn.finished",
            "message.started",
            "message.delta",
            "message.finished",
            "retry.started",
            "retry.finished",
            "agent.finished",
            "warning",
        }:
            return [self._event(raw, raw, text=_text(obj.get("text")))]
        return [self._warn("Unrecognized event retained in raw artifact", raw)]

    @staticmethod
    def _sum(records: list[dict]) -> dict:
        return {
            field: sum(record[field] for record in records if record.get(field) is not None)
            if any(record.get(field) is not None for record in records)
            else None
            for field in (*TOKEN_FIELDS, "cost_usd")
        }

    def finalize(self, exit_code: int | None) -> dict:
        usage = self._final_usage or self._sum(list(self._usage_records.values()))
        error_code, error_message = self.error_code, self.error_message
        if exit_code is None:
            error_code, error_message = "process_incomplete", "Agent process did not complete"
        elif exit_code != 0 and not error_code:
            error_code, error_message = "process_exit", f"Agent exited with code {exit_code}"
        elif not self._complete and not error_code:
            error_code, error_message = "missing_completion", "No valid terminal protocol event was received"
        observed = list(dict.fromkeys(route.get("response_model") or route["model"] for route in self.routes))
        return {
            **usage,
            "cost_provenance": "reported" if usage.get("cost_usd") is not None else "unavailable",
            "input_includes_cache": self.adapter == "codex",
            "usage_complete": (
                self._codex_usage_complete and len(self._terminal_turns) >= max(1, self.turn_index) and self._complete
                if self.adapter == "codex" else None
            ),
            "tool_calls": len(self._tool_ids),
            "turns": self.turn_index,
            "retries": self.retries,
            "configured_model": self.configured_model,
            "primary_observed_model": observed[0] if len(observed) == 1 else None,
            "final_response": self.final_response,
            "routes": list(self.routes),
            "error_code": error_code,
            "error_message": error_message,
            "parser_warnings": list(self.parser_warnings),
        }
