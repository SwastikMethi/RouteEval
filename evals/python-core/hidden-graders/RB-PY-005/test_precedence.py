from copy import deepcopy
from settings import DEFAULTS, load_config
from settings.merge import merge_settings

def test_every_precedence_pair_and_nested_merge():
    assert load_config({"retries": 4})["retries"] == 4
    assert load_config(env={"APP_RETRIES": "5"})["retries"] == 5
    assert load_config(runtime={"retries": 6})["retries"] == 6
    assert load_config({"retries": 4}, {"APP_RETRIES": "5"})["retries"] == 5
    assert load_config({"retries": 4}, runtime={"retries": 6})["retries"] == 6
    assert load_config(env={"APP_RETRIES": "5"}, runtime={"retries": 6})["retries"] == 6
    result = load_config({"database": {"host": "project", "port": 100}},
                         {"APP_DATABASE__PORT": "200"}, {"database": {"port": 300}})
    assert result["database"] == {"host": "project", "port": 300}

def test_none_and_immutable_layers(monkeypatch):
    monkeypatch.setenv("APP_RETRIES", "999")
    project = {"database": {"host": "db"}, "retries": None}
    env = {"APP_DATABASE__PORT": "44", "UNRELATED": "ignore"}
    runtime = {"database": None, "timeout": None}
    originals = deepcopy([project, env, runtime, DEFAULTS])
    result = load_config(project, env, runtime)
    assert result["database"] == {"host": "db", "port": 44}
    assert result["retries"] == 3 and result["timeout"] == 30
    result["database"]["host"] = "mutated"
    assert [project, env, runtime, DEFAULTS] == originals
    assert load_config()["retries"] == 3
    assert merge_settings({"a": {"b": 1}}, {"a": None}) == {"a": {"b": 1}}
