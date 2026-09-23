from .merge import merge_settings
from .schema import DEFAULTS
from .sources import read_environment

def load_config(project=None, env=None, runtime=None):
    return merge_settings(DEFAULTS, runtime, read_environment(env or {}), project)
