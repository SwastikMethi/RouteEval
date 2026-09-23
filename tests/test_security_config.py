"""Offline regressions for configuration, stream, and filesystem trust boundaries."""

from copy import deepcopy
from datetime import date

import pytest
import yaml
from routebench.config import Profile, Settings, load_yaml
from routebench.security import (
    REDACTED,
    StreamRedactor,
    clean_environment,
    owned_delete,
    safe_child,
    sanitize,
)


@pytest.fixture
def config_files(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "fixture").mkdir()
    (suite / "fixture" / "answer.py").write_text("value = 0\n")
    (suite / "hidden").mkdir()
    (suite / "hidden" / "test_answer.py").write_text("assert True\n")
    (suite / "reference.patch").write_text("synthetic patch\n")
    manifest = {
        "version": 1,
        "release": "1.0.0",
        "name": "Synthetic",
        "cases": [
            {
                "id": "one",
                "title": "One",
                "category": "bug",
                "prompt": "Set value to one.",
                "fixture": "fixture",
                "hidden_graders": "hidden",
                "reference_patch": "reference.patch",
                "timeout_seconds": 30,
                "scoring": [
                    {
                        "id": "functional",
                        "component": "functional",
                        "weight": 1,
                        "mandatory": True,
                        "command": ["{python}", "-m", "pytest", "-q"],
                    }
                ],
            }
        ],
    }
    (suite / "suite.yaml").write_text(yaml.safe_dump(manifest))
    config = {
        "version": 1,
        "suites": {"synthetic": "suite/suite.yaml"},
        "profiles": {"pi": {"label": "Pi", "adapter": "pi", "model": "Auto-3-API"}},
    }
    path = tmp_path / "routebench.yaml"

    def write(data=None, suite_data=None):
        path.write_text(yaml.safe_dump(config if data is None else data))
        if suite_data is not None:
            (suite / "suite.yaml").write_text(yaml.safe_dump(suite_data))
        return path

    return write, config, manifest


def test_valid_settings_preserve_native_cli_configuration_without_execution(config_files):
    write, config, _ = config_files
    config["profiles"]["pi"]["env_allowlist"] = ["REQUIRED_API_KEY"]
    config["profiles"]["pi"]["pricing"] = {
        "source": "manual",
        "effective_date": date(2026, 9, 23),
        "input_per_million": 0,
        "output_per_million": 3.5,
        "cached_input_per_million": None,
    }
    settings = Settings(write())
    assert settings.profiles["pi"]["model"] == "Auto-3-API"
    assert settings.profiles["pi"]["pricing"]["effective_date"] == "2026-09-23"
    assert settings.profiles["pi"]["env_allowlist"] == ["REQUIRED_API_KEY"]
    assert len(settings.fingerprint) == 64
    assert not settings.workspaces.exists()
    assert not settings.database.exists()


@pytest.mark.parametrize(
    "section,field,value",
    [
        ("execution", "timeout_seconds", 0),
        ("execution", "timeout_seconds", 7201),
        ("execution", "timeout_seconds", float("nan")),
        ("execution", "grader_timeout_seconds", -1),
        ("execution", "grader_timeout_seconds", float("inf")),
        ("execution", "max_concurrency", 0),
        ("execution", "max_concurrency", 17),
        ("execution", "max_concurrency", True),
        ("execution", "max_artifact_bytes", 0),
        ("execution", "max_artifact_bytes", 134217729),
        ("execution", "attempts", 2),
        ("execution", "random_seed", -1),
        ("execution", "random_seed", "42"),
        ("execution", "mode", "unsafe"),
        ("execution", "mode", []),
        ("execution", "unexpected", 1),
        ("server", "port", 0),
        ("server", "port", 65536),
        ("server", "host", "0.0.0.0"),
        ("server", "host", []),
        ("storage", "retention_days", 0),
        ("storage", "retention_days", 3651),
        ("storage", "retention_days", True),
        ("storage", "keep_workspaces", "false"),
        ("storage", "database", ""),
        ("storage", "workspaces", "$HOME/workspaces"),
    ],
)
def test_invalid_execution_storage_and_server_values_are_rejected(config_files, section, field, value):
    write, config, _ = config_files
    config[section] = {field: value}
    with pytest.raises(ValueError):
        Settings(write())


@pytest.mark.parametrize(
    "pricing",
    [
        {},
        {"source": "manual", "effective_date": "not-a-date"},
        {"source": "manual", "effective_date": "2026-09-23", "other": 1},
        *(
            {"source": "manual", "effective_date": "2026-09-23", "input_per_million": value}
            for value in (float("nan"), float("inf"), -1, True, "1")
        ),
    ],
)
def test_pricing_requires_dated_finite_known_fields(pricing):
    with pytest.raises(ValueError):
        Profile(label="Pi", adapter="pi", model="Auto-3-API", pricing=pricing)


@pytest.mark.parametrize(
    "definition",
    [
        {"model": ""},
        {"model": "line\nbreak"},
        {"timeout_seconds": True},
        {"timeout_seconds": 0},
        {"env_allowlist": ["API_KEY=value"]},
        {"env_allowlist": ["KEY", "KEY"]},
        {"api_key": "synthetic-secret"},
        {"permission_policy": {"nested": {"password": "synthetic-secret"}}},
        {"permission_policy": {"clientSecret": "synthetic-secret"}},
        {"command": ["pi", "{prompt}"]},
        {"command": ["{model}", "--mode", "json"]},
        {"adapter": "custom-jsonl", "command": ["sh", "-c", "{prompt}"]},
        {"adapter": "custom-jsonl", "command": ["agent", "--api-key", "synthetic-secret"]},
        {"adapter": "custom-jsonl", "command": ["agent", "{workspace.__class__}"]},
    ],
)
def test_profile_boundary_rejects_unsafe_commands_and_secret_fields(definition):
    with pytest.raises(ValueError) as error:
        Profile.model_validate({"label": "Profile", "adapter": "pi", "model": "Auto-3-API", **definition})
    assert "synthetic-secret" not in str(error.value)


def test_profile_mutable_defaults_do_not_share_state():
    first = Profile(label="A", adapter="pi", model="Auto-3-API")
    second = Profile(label="B", adapter="pi", model="Auto-3-API")
    first.env_allowlist.append("TEST")
    first.permission_policy["summary"] = "test"
    assert second.env_allowlist == []
    assert second.permission_policy == {}


def test_duplicate_yaml_keys_case_ids_and_nonfinite_weights(config_files, tmp_path):
    duplicate = tmp_path / "duplicate.yaml"
    duplicate.write_text("profiles:\n  pi: {}\n  pi: {}\n")
    with pytest.raises(ValueError, match="Duplicate YAML"):
        load_yaml(duplicate)
    write, _, manifest = config_files
    duplicated = deepcopy(manifest)
    duplicated["cases"].append(deepcopy(duplicated["cases"][0]))
    with pytest.raises(ValueError, match="duplicate case"):
        Settings(write(suite_data=duplicated))
    manifest["cases"][0]["scoring"][0]["weight"] = float("nan")
    with pytest.raises(ValueError):
        Settings(write(suite_data=manifest))


@pytest.mark.parametrize(
    "storage",
    [
        {"workspaces": "suite/fixture"},
        {"artifacts": "suite/hidden/output"},
        {"database": "suite/fixture/results.db"},
        {"workspaces": "suite"},
        {"artifacts": ".routebench", "workspaces": ".routebench/workspaces"},
        {"database": ".routebench/workspaces/results.db"},
        {"database": ".routebench/artifacts/results.db"},
        {"database": "routebench.yaml"},
        {"workspaces": "."},
        {"workspaces": "../.."},
    ],
)
def test_storage_never_overlaps_inputs_or_deletable_database_trees(config_files, storage):
    write, config, _ = config_files
    config["storage"] = storage
    with pytest.raises(ValueError):
        Settings(write())


def test_storage_symlinks_and_hidden_input_symlinks_are_rejected(config_files):
    write, config, _ = config_files
    path = write()
    link = path.parent / "link"
    link.symlink_to(path.parent / "suite" / "fixture")
    config["storage"] = {"workspaces": "link"}
    with pytest.raises(ValueError, match="symlink"):
        Settings(write())
    config.pop("storage")
    (path.parent / "suite" / "hidden" / "linked.py").symlink_to(
        path.parent / "suite" / "fixture" / "answer.py"
    )
    with pytest.raises(ValueError, match="Symlinks"):
        Settings(write())


def test_stream_redacts_tokens_at_every_chunk_boundary():
    text = "prefix Bearer syntheticcredential0123456789 suffix\n"
    explicit = "private-value-123456789"
    text += explicit + "\n"
    for split in range(len(text) + 1):
        redactor = StreamRedactor([explicit])
        output = redactor.feed(text[:split]) + redactor.feed(text[split:]) + redactor.flush()
        assert explicit not in output and "syntheticcredential" not in output
        assert redactor.detected
        assert output.count(REDACTED) == 2


def test_token_prefixes_inside_project_names_are_not_credentials():
    project = 'workspace-iteration-2-eval-6-flask-openapi-validation-fixture'
    token = 'sk-proj-' + 'synthetic0123456789' * 2
    text = f'{{"project":"{project}","key":"{token}"}}\n'
    for split in range(len(text) + 1):
        redactor = StreamRedactor()
        output = redactor.feed(text[:split]) + redactor.feed(text[split:]) + redactor.flush()
        assert project in output and token not in output
        assert redactor.detected and output.count(REDACTED) == 1
    redactor = StreamRedactor()
    assert redactor.feed(project) + redactor.flush() == project
    assert not redactor.detected


@pytest.mark.parametrize("limit", [24, 1024])
def test_stream_private_keys_split_across_chunks_and_oversized_headers(limit):
    text = (
        "-----BEGIN RSA PRIVATE KEY-----\nTOP_SECRET_KEY_BODY\n-----END RSA PRIVATE KEY-----\nordinary tail\n"
    )
    redactor = StreamRedactor(max_line=limit)
    output = (
        "".join(redactor.feed(text[index : index + 3]) for index in range(0, len(text), 3)) + redactor.flush()
    )
    assert "TOP_SECRET_KEY_BODY" not in output
    assert "ordinary tail" in output
    assert redactor.detected
    assert not redactor.private_key


def test_stream_bounds_oversized_lines_and_detects_discarded_secrets():
    redactor = StreamRedactor(["credential-crosses-chunks"], max_line=64)
    output = redactor.feed("x" * 1_000_000 + "credential-crosses-")
    assert len(redactor.buffer) <= 64
    assert len(redactor._discard_tail) <= 64
    output += redactor.feed("chunks\nordinary\n") + redactor.flush()
    assert output.count("[oversized line omitted]") == 1
    assert "credential" not in output
    assert "ordinary\n" in output
    assert redactor.detected


def test_authorization_prose_is_not_a_secret_and_headers_are():
    prose = 'Authorization policy allows workspace edits.\nauthorization: required\n{"authorization":"policy"}\nBearer authentication is configured.\n'
    redactor = StreamRedactor()
    assert redactor.feed(prose) + redactor.flush() == prose
    assert not redactor.detected
    header = "Authorization: Basic dXNlcjpwYXNzd29yZDEyMzQ1Njc4OTA=\n"
    assert "dXNlcj" not in sanitize(header)
    assert REDACTED in sanitize("https://example.invalid/?token=synthetic-value")


def test_owned_cleanup_refuses_symlink_targets_markers_and_unowned_paths(tmp_path):
    root = tmp_path / "workspaces"
    root.mkdir()
    target = root / "owned"
    target.mkdir()
    with pytest.raises(ValueError):
        owned_delete(root, target)
    marker = target / ".routebench-owned"
    outside_marker = tmp_path / "marker"
    outside_marker.touch()
    marker.symlink_to(outside_marker)
    with pytest.raises(ValueError):
        owned_delete(root, target)
    marker.unlink()
    marker.touch()
    link = root / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ValueError):
        owned_delete(root, link)
    with pytest.raises(ValueError):
        safe_child(root, link / "file")
    with pytest.raises(ValueError):
        owned_delete(root, root)
    owned_delete(root, target)
    assert not target.exists()
    assert outside_marker.exists()


def test_environment_inherits_only_runtime_and_explicit_values(monkeypatch):
    monkeypatch.setenv("PRIVATE_API_KEY", "synthetic-secret")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/synthetic/socket")
    monkeypatch.setenv("HOME", "/synthetic/home")
    assert "PRIVATE_API_KEY" not in clean_environment()
    assert "SSH_AUTH_SOCK" not in clean_environment()
    assert clean_environment()["HOME"] == "/synthetic/home"
    assert clean_environment(["PRIVATE_API_KEY"])["PRIVATE_API_KEY"] == "synthetic-secret"
