"""Narrow filesystem ownership, environment policy, and pre-storage redaction."""

import os
import re
import shutil
from pathlib import Path

SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH", re.I)
TOKEN_PATTERNS = [
    re.compile(r"(?<![A-Za-z0-9_])(?:sk-(?:ant-|proj-)?|gh[pousr]_|github_pat_|AIza)[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)\bauthorization[\"']?\s*[:=]\s*[\"']?(?:bearer|basic)\s+[A-Za-z0-9._~+/-]{16,}={0,2}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{20,}={0,2}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(?:[?&](?:api_?key|token|secret|password|credential)=)[^&#\s\"']+"),
]
REDACTED = "[REDACTED: possible token]"
PRIVATE_MARKER = re.compile(r"-----([A-Z]+) (?:[A-Z]+ )?PRIVATE KEY-----")


def safe_child(root: Path, name: str | Path) -> Path:
    root = Path(root).resolve()
    raw = root / name
    candidate = raw.resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError("Path must be a descendant of its configured root")
    for part in (raw, *raw.parents):
        if part.resolve() == root:
            break
        if part.is_symlink():
            raise ValueError("Paths beneath a configured root cannot traverse symlinks")
    return candidate


def reject_symlinks(root: Path) -> None:
    if root.is_symlink() or any(p.is_symlink() for p in root.rglob("*")):
        raise ValueError("Symlinks are not permitted in benchmark fixtures or grading inputs")


def clean_environment(allowlist: list[str] | None = None) -> dict[str, str]:
    ordinary = {"PATH", "HOME", "USER", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TERM", "SHELL"}
    env = {k: v for k, v in os.environ.items() if k in ordinary and not SECRET_NAME.search(k)}
    for key in allowlist or ():
        if key in os.environ:
            env[key] = os.environ[key]
    env.update({"NO_COLOR": "1", "PYTHONDONTWRITEBYTECODE": "1"})
    return env


class StreamRedactor:
    """Buffer logical lines so split tokens cannot reach disk before inspection."""

    def __init__(self, secrets=(), max_line=1024 * 1024):
        if not isinstance(max_line, int) or isinstance(max_line, bool) or max_line < 1:
            raise ValueError("Redaction line limit must be a positive integer")
        self.secrets = tuple(s for s in secrets if isinstance(s, str) and len(s) >= 6)
        self.buffer = ""
        self.max_line = max_line
        self.discarding = False
        self.private_key = False
        self.detected = False
        self._marker_tail = ""
        self._line_private = False
        self._discard_tail = ""
        self._overlap = min(max_line, max([128, *(len(secret) for secret in self.secrets)]))

    def _scan_private(self, text):
        combined = self._marker_tail + text
        private = self.private_key
        end = 0
        for match in PRIVATE_MARKER.finditer(combined):
            if match[1] == "BEGIN":
                self.private_key = private = True
            elif match[1] == "END":
                private = True
                self.private_key = False
            end = match.end()
        self._marker_tail = combined[end:][-80:]
        if private:
            self.detected = True
        return private

    def _redact_tokens(self, text):
        original = text
        for secret in self.secrets:
            text = text.replace(secret, REDACTED)
        for pattern in TOKEN_PATTERNS:
            text = pattern.sub(REDACTED, text)
        self.detected |= text != original
        return text

    def clean(self, text: str) -> str:
        if self._scan_private(text):
            return REDACTED + ("\n" if text.endswith("\n") else "")
        return self._redact_tokens(text)

    def feed(self, chunk: str) -> str:
        output = []
        position = 0
        while position < len(chunk):
            newline = chunk.find("\n", position, position + 16384)
            end = newline + 1 if newline != -1 else min(position + 16384, len(chunk))
            part = chunk[position:end]
            position = end
            self._line_private |= self._scan_private(part)
            if self.discarding:
                scan = self._discard_tail + part
                self._redact_tokens(scan)
                self._discard_tail = scan[-self._overlap :]
            elif len(self.buffer) + len(part.rstrip("\n")) > self.max_line:
                scan = self.buffer + part
                self._redact_tokens(scan)
                self._discard_tail = scan[-self._overlap :]
                self.buffer = ""
                self.discarding = True
                output.append("[oversized line omitted]\n")
            else:
                self.buffer += part
            if part.endswith("\n"):
                if not self.discarding:
                    output.append(REDACTED + "\n" if self._line_private else self._redact_tokens(self.buffer))
                self.buffer = self._discard_tail = self._marker_tail = ""
                self.discarding = self._line_private = False
        return "".join(output)

    def flush(self) -> str:
        value = (
            "" if self.discarding else (REDACTED if self._line_private else self._redact_tokens(self.buffer))
        )
        self.buffer = self._discard_tail = self._marker_tail = ""
        self.discarding = self._line_private = False
        return value


def sanitize(value, secrets=(), paths=True):
    if isinstance(value, dict):
        return {
            k: sanitize(v, secrets, paths) for k, v in value.items() if k not in {"environment", "env_values"}
        }
    if isinstance(value, list):
        return [sanitize(v, secrets, paths) for v in value]
    if isinstance(value, str):
        redactor = StreamRedactor(secrets)
        value = redactor.feed(value) + redactor.flush()
        if paths:
            value = value.replace(str(Path.home()), "~")
        return value
    return value


def owned_delete(root: Path, target: Path, marker=".routebench-owned") -> None:
    if Path(marker).name != marker or marker in {"", ".", ".."}:
        raise ValueError("Cleanup marker must be a filename")
    target = safe_child(root, target)
    if not target.is_dir() or (target / marker).is_symlink() or not (target / marker).is_file():
        raise ValueError("Refusing cleanup of an unowned directory")
    shutil.rmtree(target)
