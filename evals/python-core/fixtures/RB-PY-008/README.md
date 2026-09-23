# Artifact paths
`artifacts.normalize_path(root, relative)` returns a resolved pathlib.Path
strictly inside resolved root. root accepts a pathlib.Path or path string.
relative must be a nonempty string naming an artifact, not the root itself.
Allow nested paths, Unicode, spaces, repeated forward slashes and dot segments.
Normalize '..' segments only when the final resolved path stays inside root.
Reject escaping paths, POSIX absolute paths, Windows drive-prefixed paths
(including drive-relative C:foo), any backslash, NUL bytes, and non-string
inputs with ValueError. A name beginning with a dot is otherwise ordinary.
Containment must use path components, not a string prefix. Resolve existing
symlinks: links staying inside root are allowed, links escaping root are not.
The target need not exist. Do not create directories or files. This normalization
helper does not promise race-free file opening against concurrent symlink edits.
`ArtifactStore.path_for(name)` must use the same rules. Preserve public APIs.
Change only `artifacts/`; run `python -m pytest -q tests`.
