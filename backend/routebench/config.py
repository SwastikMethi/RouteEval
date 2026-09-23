import hashlib
import json
import math
import os
import re
from pathlib import Path
from string import Formatter

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .security import PRIVATE_MARKER, TOKEN_PATTERNS, reject_symlinks, safe_child, sanitize

ROOT = Path(__file__).resolve().parents[2]
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
SECRET_FIELD = re.compile(
    r"(?:api[_-]?key|(?:access|refresh|bearer|auth|session)[_-]?token|client[_-]?secret|private[_-]?key|password|credentials?|authorization)|(?:^|[_-])(?:token|secret)(?:$|[_-])",
    re.I,
)


def _reject_secrets(value, active=None):
    active = set() if active is None else active
    if isinstance(value, (dict, list)):
        if id(value) in active:
            raise ValueError("Recursive configuration aliases are not supported")
        active.add(id(value))
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise ValueError("Configuration mapping keys must be strings")
                if SECRET_FIELD.search(key):
                    raise ValueError("Credential fields are not permitted in configuration")
                _reject_secrets(key, active)
                _reject_secrets(item, active)
        else:
            for item in value:
                _reject_secrets(item, active)
        active.remove(id(value))
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Configuration numbers must be finite")
    elif isinstance(value, str) and (
        PRIVATE_MARKER.search(value) or any(pattern.search(value) for pattern in TOKEN_PATTERNS)
    ):
        raise ValueError("Credential values are not permitted in configuration")


def _section(data, name, defaults):
    value = data.get(name, {})
    if not isinstance(value, dict) or set(value) - set(defaults):
        raise ValueError(f"Invalid or unknown {name} configuration fields")
    return {**defaults, **value}


def _integer(value, minimum, maximum, label):
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer from {minimum} to {maximum}")
    return value


def _unit(value, label):
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError(f"{label} must be a finite number from zero to one")
    return value


class UniqueLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ValueError("YAML mapping keys must be strings")
        if key in result:
            raise ValueError("Duplicate YAML mapping key")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def load_yaml(path):
    if Path(path).stat().st_size > 2 * 1024 * 1024:
        raise ValueError("Configuration exceeds 2 MiB")
    value = yaml.load(Path(path).read_text(), Loader=UniqueLoader)
    _reject_secrets(value)
    return value


def fingerprint(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str).encode()
    ).hexdigest()


def tree_hash(root):
    reject_symlinks(root)
    content = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not any(
            p in {".git", "__pycache__", ".pytest_cache"} for p in path.relative_to(root).parts
        ):
            content.append((str(path.relative_to(root)), hashlib.sha256(path.read_bytes()).hexdigest()))
    return fingerprint(content)


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)
    label: str = Field(min_length=1, max_length=200)
    adapter: str
    model: str = Field(min_length=1, max_length=300)
    enabled: bool = True
    timeout_seconds: int = Field(default=900, ge=1, le=7200)
    command: list[str] | None = None
    env_allowlist: list[str] = Field(default_factory=list)
    permission_policy: dict = Field(default_factory=dict)
    pricing: dict | None = None

    @model_validator(mode="before")
    @classmethod
    def no_credentials(cls, value):
        _reject_secrets(value)
        return value

    @field_validator("label", "model")
    @classmethod
    def valid_text(cls, value):
        if not value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("Profile label and model must be nonempty single-line text")
        return value

    @field_validator("env_allowlist")
    @classmethod
    def valid_allowlist(cls, value):
        if (
            len(value) > 100
            or len(value) != len(set(value))
            or any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) for name in value)
        ):
            raise ValueError("Environment allowlist must contain distinct variable names")
        return value

    @field_validator("adapter")
    @classmethod
    def adapter_supported(cls, value):
        if value not in {"pi", "claude", "codex", "custom-jsonl", "mock"}:
            raise ValueError("Unknown adapter")
        return value

    @field_validator("command")
    @classmethod
    def valid_command(cls, command):
        if command is None:
            return command
        if (
            not command
            or len(command) > 200
            or any(not arg or "\0" in arg or len(arg) > 32768 for arg in command)
        ):
            raise ValueError("Command must contain nonempty arguments")
        for arg in command:
            for _, field, spec, conv in Formatter().parse(arg):
                if field is not None and (
                    field not in {"model", "prompt", "workspace", "attempt_id"} or spec or conv
                ):
                    raise ValueError("Unsupported command placeholder")
            if re.search(r"(?i)(api[-_]?key|bearer[-_]?token|auth[-_]?token)", arg):
                raise ValueError("Credentials cannot be supplied as command arguments")
        return command

    @field_validator("pricing")
    @classmethod
    def valid_pricing(cls, pricing):
        if pricing is not None:
            from datetime import date

            allowed = {
                "source",
                "effective_date",
                "input_per_million",
                "output_per_million",
                "cached_input_per_million",
                "cache_write_per_million",
            }
            if set(pricing) - allowed:
                raise ValueError("Unknown pricing fields")
            date.fromisoformat(str(pricing.get("effective_date", "")))
            if not isinstance(pricing.get("source"), str) or not pricing["source"].strip():
                raise ValueError("Pricing requires a source")
            pricing = {**pricing, "effective_date": str(pricing["effective_date"])}
            for key in (
                "input_per_million",
                "output_per_million",
                "cached_input_per_million",
                "cache_write_per_million",
            ):
                value = pricing.get(key)
                if value is not None and (
                    not isinstance(value, (float, int))
                    or isinstance(value, bool)
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise ValueError("Prices must be finite nonnegative numbers or null")
        return pricing

    @model_validator(mode="after")
    def command_policy(self):
        if self.adapter != "mock":
            from .adapters import build_command

            build_command(
                self.model_dump(),
                "configuration-validation",
                Path("/tmp/routebench-validation"),
                "validation",
            )
        elif self.command:
            raise ValueError("Mock profiles cannot override execution commands")
        return self


class Settings:
    def __init__(self, path: Path | str | None = None):
        self.path = Path(path or os.environ.get("ROUTEBENCH_CONFIG", ROOT / "routebench.yaml"))
        if not self.path.exists() and path is None and "ROUTEBENCH_CONFIG" not in os.environ:
            self.path = ROOT / "routebench.example.yaml"
        self.path = self.path.resolve()
        data = load_yaml(self.path)
        if not isinstance(data, dict) or type(data.get("version")) is not int or data["version"] != 1:
            raise ValueError("Expected RouteBench configuration version 1")
        unknown = set(data) - {"version", "server", "storage", "execution", "suites", "profiles"}
        if unknown:
            raise ValueError(f"Unknown config fields: {', '.join(sorted(unknown))}")
        self.base = self.path.parent
        self.server = _section(data, "server", {"host": "127.0.0.1", "port": 8765})
        if not isinstance(self.server["host"], str) or self.server["host"] not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }:
            raise ValueError("V1 only supports a loopback server address")
        _integer(self.server["port"], 1, 65535, "Server port")
        storage = _section(
            data,
            "storage",
            {
                "database": ".routebench/routebench.db",
                "artifacts": ".routebench/artifacts",
                "workspaces": ".routebench/workspaces",
                "keep_workspaces": False,
                "retention_days": 30,
            },
        )
        _integer(storage["retention_days"], 1, 3650, "Retention days")
        if not isinstance(storage["keep_workspaces"], bool):
            raise ValueError("keep_workspaces must be a boolean")
        self.storage = storage
        self.database = self.resolve(storage["database"])
        self.artifacts = self.resolve(storage["artifacts"])
        self.workspaces = self.resolve(storage["workspaces"])
        for root in (self.artifacts, self.workspaces):
            if root in {Path("/"), Path.home()} or self.base.is_relative_to(root):
                raise ValueError("Storage roots must be narrow, dedicated directories")
        if (
            self.artifacts == self.workspaces
            or self.artifacts.is_relative_to(self.workspaces)
            or self.workspaces.is_relative_to(self.artifacts)
        ):
            raise ValueError("Artifacts and workspaces must use separate directories")
        if any(
            self.database.is_relative_to(root) or root.is_relative_to(self.database)
            for root in (self.artifacts, self.workspaces)
        ):
            raise ValueError("Database must be separate from artifact and workspace directories")
        if self.database.exists() and not self.database.is_file():
            raise ValueError("Database path must name a file")
        if any(root.exists() and not root.is_dir() for root in (self.artifacts, self.workspaces)):
            raise ValueError("Artifact and workspace roots must name directories")
        self._check_source_storage(self.path)
        self.execution = _section(
            data,
            "execution",
            {
                "mode": "sequential",
                "timeout_seconds": 900,
                "attempts": 1,
                "random_seed": 42,
                "max_concurrency": 2,
                "max_artifact_bytes": 10485760,
                "grader_timeout_seconds": 60,
            },
        )
        if not isinstance(self.execution["mode"], str) or self.execution["mode"] not in {
            "sequential",
            "parallel",
        }:
            raise ValueError("Execution mode must be sequential or parallel")
        for field, low, high in [
            ("timeout_seconds", 1, 7200),
            ("grader_timeout_seconds", 1, 7200),
            ("max_concurrency", 1, 16),
            ("max_artifact_bytes", 1, 134217728),
            ("random_seed", 0, 4294967295),
            ("attempts", 1, 3),
        ]:
            _integer(self.execution[field], low, high, field)
        if self.execution["attempts"] not in {1, 3}:
            raise ValueError("Attempts must be one or three")
        if not isinstance(data.get("profiles", {}), dict) or not isinstance(data.get("suites", {}), dict):
            raise ValueError("Profiles and suites must be mappings")
        self.profiles = {}
        for ident, definition in data.get("profiles", {}).items():
            if not ID.fullmatch(ident):
                raise ValueError("Invalid profile ID")
            parsed = Profile.model_validate(definition).model_dump()
            if parsed["adapter"] == "mock" and os.environ.get("ROUTEBENCH_MOCK") != "1":
                raise ValueError("Mock adapter requires ROUTEBENCH_MOCK=1")
            self.profiles[ident] = {"id": ident, **parsed}
        if os.environ.get("ROUTEBENCH_MOCK") == "1":
            self.profiles = {
                f"mock-{name}": {
                    "id": f"mock-{name}",
                    **Profile(label=label, adapter="mock", model=name).model_dump(),
                }
                for name, label in [("reference", "Reference solution"), ("baseline", "Unchanged baseline")]
            }
        self.suite_specs = data.get("suites", {})
        self.suites = {ident: self.load_suite(ident, spec) for ident, spec in self.suite_specs.items()}
        self.fingerprint = fingerprint(sanitize(data))

    def resolve(self, value):
        if (
            not isinstance(value, str)
            or not value.strip()
            or re.search(r"[\0\r\n*?$]", value)
            or value.startswith("~")
        ):
            raise ValueError(
                "Paths must be explicit nonempty paths without globs or environment placeholders"
            )
        path = self.base / value
        if path.is_symlink():
            raise ValueError("Configured paths cannot name symlinks")
        return path.resolve()

    def _check_source_storage(self, source):
        for root in (self.database, self.artifacts, self.workspaces):
            if source.is_relative_to(root) or root.is_relative_to(source):
                raise ValueError(
                    "Storage must not overlap suite manifests, fixtures, hidden graders, or references"
                )

    def load_suite(self, ident, spec):
        if not isinstance(ident, str) or not ID.fullmatch(ident):
            raise ValueError("Invalid suite ID")
        if not isinstance(spec, str) or not spec:
            raise ValueError("Suite location must be a nonempty string")
        location, _, tag = spec.partition("#")
        manifest = self.resolve(location)
        self._check_source_storage(manifest)
        raw = load_yaml(manifest)
        if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
            raise ValueError("Unsupported suite manifest version")
        if not isinstance(raw.get("cases"), list) or not raw["cases"]:
            raise ValueError("Suite requires a nonempty case list")
        cases = []
        seen = set()
        for original in raw["cases"]:
            if not isinstance(original, dict):
                raise ValueError("Suite cases must be mappings")
            case = dict(original)
            if not isinstance(case.get("id"), str) or not ID.fullmatch(case["id"]) or case["id"] in seen:
                raise ValueError("Invalid or duplicate case ID")
            seen.add(case["id"])
            for key in ("fixture", "hidden_graders", "reference_patch"):
                if not isinstance(case.get(key), str) or not case[key]:
                    raise ValueError("Case input paths must be nonempty strings")
                case[key] = str(safe_child(manifest.parent, case[key]))
                self._check_source_storage(Path(case[key]))
                if not Path(case[key]).exists():
                    raise ValueError(f"Missing {key} for {case['id']}")
            fixture = Path(case["fixture"])
            hidden = Path(case["hidden_graders"])
            if not fixture.is_dir() or not hidden.is_dir() or not Path(case["reference_patch"]).is_file():
                raise ValueError(
                    "Fixtures and hidden graders must be directories; reference patches must be files"
                )
            if hidden.is_relative_to(fixture) or Path(case["reference_patch"]).is_relative_to(fixture):
                raise ValueError("Hidden graders and references must remain outside fixtures")
            reject_symlinks(fixture)
            reject_symlinks(hidden)
            if (fixture / "__hidden__").exists() or (fixture / ".routebench-owned").exists():
                raise ValueError("Fixture contains reserved files")
            graders = case["scoring"]
            if (
                not isinstance(graders, list)
                or not graders
                or not all(isinstance(grader, dict) for grader in graders)
            ):
                raise ValueError("Cases require nonempty grader lists")
            if abs(sum(_unit(g.get("weight"), "Grader weight") for g in graders) - 1.0) > 1e-8:
                raise ValueError("Grader weights must be nonnegative and sum to one")
            for grader in graders:
                if not isinstance(grader.get("id"), str) or not ID.fullmatch(grader["id"]):
                    raise ValueError("Invalid grader ID")
                if "command" in grader and (
                    not isinstance(grader["command"], list)
                    or not grader["command"]
                    or not all(isinstance(a, str) and a and "\0" not in a for a in grader["command"])
                ):
                    raise ValueError("Grader commands must be argument arrays")
            if len({g["id"] for g in graders}) != len(graders):
                raise ValueError("Duplicate grader ID")
            if not isinstance(case.get("prompt"), str) or not case["prompt"].strip():
                raise ValueError("Cases require a nonempty prompt")
            _integer(case.get("timeout_seconds", 900), 1, 7200, "Case timeout")
            _unit(case.get("pass_threshold", raw.get("pass_threshold", 1.0)), "Pass threshold")
            case.update(
                fixture_hash=tree_hash(fixture),
                hidden_hash=tree_hash(hidden),
                prompt_hash=fingerprint(case["prompt"]),
                pass_threshold=case.get("pass_threshold", raw.get("pass_threshold", 1.0)),
            )
            if not tag or tag in case.get("tags", []):
                cases.append(case)
        if not cases:
            raise ValueError("Suite contains no selected cases")
        stable = [
            {k: v for k, v in c.items() if k not in {"fixture", "hidden_graders", "reference_patch"}}
            for c in cases
        ]
        return {
            "id": ident,
            "name": raw["name"] + (" · " + tag.replace("-", " ").title() if tag else ""),
            "version": str(raw["release"]),
            "hash": fingerprint(stable),
            "description": (
                f"{len(cases)} {'task' if len(cases) == 1 else 'tasks'} selected from {raw['name']}."
                if tag else raw.get("description", "")
            ),
            "cases": cases,
        }

    def public_suite(self, suite):
        return {
            **suite,
            "cases": [
                {
                    k: v
                    for k, v in c.items()
                    if k not in {"fixture", "hidden_graders", "reference_patch", "scoring"}
                }
                for c in suite["cases"]
            ],
        }
