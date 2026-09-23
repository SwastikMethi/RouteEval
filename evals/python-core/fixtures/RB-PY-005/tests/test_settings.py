from settings import DEFAULTS, load_config
from settings.merge import merge_settings
from settings.sources import read_environment

def test_defaults_and_existing_helpers():
    assert load_config() == DEFAULTS
    assert load_config({"retries": 4})["retries"] == 4
    assert read_environment({"APP_TIMEOUT": "9"}) == {"timeout": 9}
    assert merge_settings({"a": {"x": 1}}, {"a": {"y": 2}}) == {"a": {"x": 1, "y": 2}}
