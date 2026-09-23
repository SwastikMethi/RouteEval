"""Fresh worktrees and canonical patches independent of candidate Git state."""

import hashlib
import os
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .config import ID, fingerprint
from .security import clean_environment, owned_delete, reject_symlinks, safe_child

MARKER = ".routebench-owned"
IGNORED = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_TOTAL_BYTES = 100 * 1024 * 1024
MAX_DIFF_BYTES = 10 * 1024 * 1024
MAX_FILES = 10_000


@dataclass
class _Baseline:
    root: Path
    files: dict[str, tuple[bytes, int]]
    tree: str


# ponytail: baselines live for one server lifetime; persisted snapshots are needed for restart/resume.
_BASELINES: dict[Path, _Baseline] = {}


class InvalidCandidateOutput(ValueError):
    """The agent produced an unsafe or uninspectable candidate, not a harness failure."""


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    """Read regular files only, ignoring caches but never candidate ignore rules."""
    reject_symlinks(root)
    if not root.is_dir():
        raise ValueError("Candidate workspace is missing")
    result = {}
    total = 0
    for directory, dirs, files in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in IGNORED)
        for name in sorted(files):
            if name in IGNORED:
                continue
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == MARKER:
                continue
            info = path.stat()
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("Only regular candidate files are permitted")
            if (
                info.st_size > MAX_FILE_BYTES
                or total + info.st_size > MAX_TOTAL_BYTES
                or len(result) >= MAX_FILES
            ):
                raise ValueError("Candidate snapshot exceeds the file or total capture limit")
            with path.open("rb") as stream:
                content = stream.read(MAX_FILE_BYTES + 1)
            total += len(content)
            if len(content) > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise ValueError("Candidate snapshot exceeds the capture limit")
            result[relative] = (content, 0o100755 if info.st_mode & 0o111 else 0o100644)
    return result


def write_snapshot(files: dict, target: Path) -> None:
    for name, (content, mode) in files.items():
        path = safe_child(target, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        path.chmod(0o755 if mode == 0o100755 else 0o644)


def _git(directory: Path, *args: str, data: bytes | None = None, output=None) -> bytes:
    env = clean_environment()
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_SYSTEM": os.devnull,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_ATTR_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        }
    )
    command = [
        "git",
        "-c",
        f"core.hooksPath={os.devnull}",
        "-c",
        "core.fsmonitor=false",
        "-c",
        f"core.attributesFile={os.devnull}",
        "-c",
        f"core.excludesFile={os.devnull}",
        "-c",
        "commit.gpgsign=false",
        "-c",
        "user.name=RouteBench",
        "-c",
        "user.email=routebench@localhost",
        "-c",
        "protocol.allow=never",
        *args,
    ]
    result = subprocess.run(
        command,
        cwd=directory,
        env=env,
        input=data,
        stdout=output if output is not None else subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        check=False,
    )
    if result.returncode:
        raise RuntimeError("Git snapshot operation failed: " + result.stderr.decode(errors="replace")[:2000])
    return result.stdout or b""


def _tree(directory: Path, files: dict[str, tuple[bytes, int]]) -> str:
    """Write blobs directly: .gitattributes, filters, and the candidate index never execute."""
    directories: dict[str, dict] = {"": {}}
    for name, (content, mode) in sorted(files.items()):
        parts = name.split("/")
        parent = ""
        for part in parts[:-1]:
            child = f"{parent}/{part}" if parent else part
            directories.setdefault(child, {})
            directories[parent][part] = ("tree", child)
            parent = child
        blob = _git(directory, "hash-object", "-w", "--stdin", data=content).decode().strip()
        directories[parent][parts[-1]] = ("blob", mode, blob)
    hashes = {}
    for parent in sorted(directories, key=lambda name: name.count("/") + bool(name), reverse=True):
        entries = []
        for name, item in directories[parent].items():
            mode, kind, digest = (
                ("040000", "tree", hashes[item[1]])
                if item[0] == "tree"
                else (f"{item[1]:o}", "blob", item[2])
            )
            entries.append(f"{mode} {kind} {digest}\t".encode() + os.fsencode(name) + b"\0")
        hashes[parent] = _git(directory, "mktree", "-z", data=b"".join(entries)).decode().strip()
    return hashes[""]


def prepare(case: dict, workspace_root: Path, run_id: str, attempt_id: str) -> Path:
    if not ID.fullmatch(run_id) or not ID.fullmatch(attempt_id):
        raise ValueError("Invalid run or attempt ID")
    fixture = Path(case["fixture"])
    reject_symlinks(fixture)
    if (fixture / MARKER).exists() or (fixture / "__hidden__").exists():
        raise ValueError("Fixture contains a reserved path")
    files = snapshot(fixture)
    copied_hash = fingerprint(
        [(name, hashlib.sha256(content).hexdigest()) for name, (content, _) in sorted(files.items())]
    )
    if copied_hash != case["fixture_hash"]:
        raise ValueError("Fixture content hash changed after suite loading")
    root = Path(workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    destination = safe_child(root, f"{run_id}--{attempt_id}")
    destination.mkdir()
    (destination / MARKER).write_text("RouteBench agent workspace\n")
    try:
        write_snapshot(files, destination)
        # Check the copied inputs before adding harness metadata or Git files.
        copied = snapshot(destination)
        if copied != files:
            raise ValueError("Fixture changed while copying the workspace")
        _git(destination, "init", "--quiet", "--template=", "--initial-branch=main")
        baseline_tree = _tree(destination, files)
        _git(destination, "read-tree", baseline_tree)
        _git(destination, "commit", "--quiet", "--allow-empty", "-m", "RouteBench fixture baseline")
        (destination / ".git/info").mkdir(exist_ok=True)
        (destination / ".git/info/exclude").write_text(f"/{MARKER}\n")
        _BASELINES[destination] = _Baseline(root, files, baseline_tree)
        return destination
    except BaseException:
        owned_delete(root, destination)
        raise


def capture(workspace: Path) -> dict:
    try:
        reject_symlinks(workspace)
    except ValueError as exc:
        raise InvalidCandidateOutput(str(exc)) from exc
    workspace = workspace.resolve()
    baseline = _BASELINES.get(workspace)
    if baseline is None:
        raise ValueError("No prepared baseline is available for this workspace")
    try:
        current = snapshot(workspace)
    except (ValueError, FileNotFoundError, NotADirectoryError, PermissionError) as exc:
        raise InvalidCandidateOutput(str(exc)) from exc
    changed = sorted(
        name
        for name in baseline.files.keys() | current.keys()
        if baseline.files.get(name) != current.get(name)
    )
    temporary = Path(tempfile.mkdtemp(prefix=".capture-", dir=baseline.root))
    (temporary / MARKER).write_text("RouteBench capture scratch\n")
    try:
        _git(temporary, "init", "--quiet", "--bare", "--template=")
        before = _tree(temporary, baseline.files)
        after = _tree(temporary, current)
        if before != baseline.tree:
            raise RuntimeError("Prepared baseline tree could not be reproduced")
        options = ("--no-ext-diff", "--no-textconv", "--no-renames", before, after)
        with (temporary / "patch-output").open("w+b") as output:
            _git(temporary, "diff", "--binary", *options, output=output)
            output.seek(0)
            raw = output.read(MAX_DIFF_BYTES + 1)
        truncated = len(raw) > MAX_DIFF_BYTES
        diff = raw[:MAX_DIFF_BYTES].decode(errors="replace")
        if truncated:
            diff += (
                "\n[RouteBench: diff truncated at 10 MiB; tree IDs and change metrics cover the full patch]\n"
            )
        counts = _git(temporary, "diff", "--numstat", "-z", *options)
        added = removed = 0
        for record in counts.split(b"\0"):
            if record:
                plus, minus, _ = record.split(b"\t", 2)
                added += int(plus) if plus != b"-" else 0
                removed += int(minus) if minus != b"-" else 0
        return {
            "diff": diff,
            "files_changed": len(changed),
            "lines_added": added,
            "lines_removed": removed,
            "changed_paths": changed,
            "baseline_tree": before,
            "post_tree": after,
            "diff_truncated": truncated,
        }
    finally:
        owned_delete(baseline.root, temporary)


def release(workspace: Path) -> None:
    _BASELINES.pop(Path(workspace).resolve(), None)
