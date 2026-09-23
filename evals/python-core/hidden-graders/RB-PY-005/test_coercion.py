import pytest
from settings import ConfigError, load_config
from settings.schema import coerce_value
from settings.sources import read_environment

def test_coercion_at_every_entry_point():
    for value in [True, "true", " TRUE ", "1", "yes", "ON"]:
        assert coerce_value("debug", value) is True
        assert load_config({"debug": value})["debug"] is True
    for value in [False, "false", " FALSE ", "0", "no", "Off"]:
        assert load_config(runtime={"debug": value})["debug"] is False
        assert read_environment({"APP_DEBUG": value})["debug"] is False
    assert load_config({"retries": " +0 "}, {"APP_TIMEOUT": " 17 "}, {"database": {"port": "65535"}})["database"]["port"] == 65535
    assert read_environment({"APP_DATABASE__HOST": " db "}) == {"database": {"host": " db "}}

@pytest.mark.parametrize("key,value", [("debug", "maybe"), ("debug", 1), ("retries", True), ("retries", 1.9), ("retries", "1.0"), ("retries", "٣"), ("retries", -1), ("timeout", 0), ("database.port", 0), ("database.port", 65536), ("database.host", ""), ("database.host", 9)])
def test_invalid_values_have_context(key, value):
    with pytest.raises(ConfigError) as error:
        coerce_value(key, value)
    assert key in str(error.value) and len(str(error.value)) > len(key) + 5
    data = {key: value} if "." not in key else {"database": {key.split(".")[1]: value}}
    with pytest.raises(ConfigError):
        load_config(data)

def test_unknown_keys_invalid_nesting_and_overridden_invalid_values():
    for data, key in [({"unknown": 1}, "unknown"), ({"database": {"user": "x"}}, "database.user"), ({"database": "x"}, "database")]:
        with pytest.raises(ConfigError) as error:
            load_config(data)
        assert key in str(error.value)
    with pytest.raises(ConfigError):
        load_config({"retries": -1}, runtime={"retries": 8})
    with pytest.raises(ConfigError):
        read_environment({"APP_DATABASE__PORT": "bad"})
