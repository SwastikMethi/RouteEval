# Layered settings
Public API: `settings.load_config(project=None, env=None, runtime=None)` returns
a fresh nested dictionary. Layers have increasing priority: DEFAULTS < project
< environment < runtime. Merge per leaf so changing database.port preserves its
host. None means absent, both for a whole layer and for individual values.
`env` is an explicitly supplied mapping: None means empty, never os.environ.

Schema (and defaults): debug=False (bool); retries=3 (integer >=0); timeout=30
(integer >0); database.host='localhost' (nonempty string); database.port=5432
(integer 1..65535). Integers accept int except bool, or signed ASCII decimal
strings with surrounding whitespace; never truncate floats. Booleans accept
bool or case-insensitive stripped 'true', 'false', '1', '0', 'yes', 'no', 'on',
'off'; numbers are not booleans. Host strings retain their original contents.
Unknown project/runtime keys and invalid values raise ConfigError (a ValueError)
with the dotted key and a useful expected-type/range description. Validate every
supplied non-None value, even if a later layer overrides it. Inputs and DEFAULTS
must remain unchanged. A non-mapping database value is invalid.

Environment names: APP_DEBUG, APP_RETRIES, APP_TIMEOUT, APP_DATABASE__HOST,
APP_DATABASE__PORT. Unrecognized variables are ignored. Preserve existing helper
APIs `settings.sources.read_environment(env)` (returns nested typed overrides),
`settings.schema.coerce_value(key, value)` (coerces one dotted schema key), and
`settings.merge.merge_settings(*layers)` (deep merge, no coercion, skips None).
Centralize coercion in schema.coerce_value and use it across entry points.
Change only `settings/`. Run `python -m pytest -q tests`.
