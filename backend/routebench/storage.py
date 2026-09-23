"""SQLite summaries and a transactionally ordered event journal."""

import fcntl
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .security import safe_child, sanitize


def now():
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, database: Path, artifacts: Path):
        database.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        artifacts.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = database.with_suffix(database.suffix + ".lock").open("a")
        os.chmod(self.lock.name, 0o600)
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise ValueError("This database is already in use by another RouteBench process") from None
        self.artifacts = artifacts
        self.db = sqlite3.connect(database, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA busy_timeout=5000")
        os.chmod(database, 0o600)
        self.migrate()

    def migrate(self):
        self.db.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY)")
        if not self.db.execute("SELECT 1 FROM schema_migrations WHERE version=1").fetchone():
            self.db.executescript("""
                BEGIN;
                CREATE TABLE runs(id TEXT PRIMARY KEY,data TEXT NOT NULL);
                CREATE TABLE attempts(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,data TEXT NOT NULL);
                CREATE INDEX attempts_run ON attempts(run_id);
                CREATE TABLE events(run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,sequence INTEGER NOT NULL,type TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(run_id,sequence));
                CREATE TABLE artifacts(id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,data TEXT NOT NULL);
                INSERT INTO schema_migrations VALUES(1);
                COMMIT;
            """)

    def close(self):
        self.db.close()
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        self.lock.close()

    def save_run(self, data, event=None):
        with self.db:
            self.db.execute(
                "INSERT INTO runs(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (data["id"], json.dumps(data)),
            )
            if event:
                self._event(data["id"], event, {"run_id": data["id"], "status": data["status"]})

    def save_attempt(self, data, event=None):
        with self.db:
            self.db.execute(
                "INSERT INTO attempts(id,run_id,data) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (data["id"], data["run_id"], json.dumps(data)),
            )
            if event:
                self._event(
                    data["run_id"],
                    event,
                    {
                        "run_id": data["run_id"],
                        "attempt_id": data["id"],
                        "status": data["status"],
                        "score": data.get("score"),
                    },
                )

    def _event(self, run_id, kind, data):
        seq = self.db.execute(
            "SELECT COALESCE(MAX(sequence),0)+1 FROM events WHERE run_id=?", (run_id,)
        ).fetchone()[0]
        self.db.execute("INSERT INTO events VALUES(?,?,?,?)", (run_id, seq, kind, json.dumps(sanitize(data))))
        return seq

    def event(self, run_id, kind, data):
        with self.db:
            return self._event(run_id, kind, data)

    def events(self, run_id, after=0, limit=500):
        return [
            {"sequence": row["sequence"], "type": row["type"], "data": json.loads(row["data"])}
            for row in self.db.execute(
                "SELECT * FROM events WHERE run_id=? AND sequence>? ORDER BY sequence LIMIT ?",
                (run_id, after, limit),
            )
        ]

    def run(self, ident):
        row = self.db.execute("SELECT data FROM runs WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return json.loads(row[0])

    def runs(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT data FROM runs ORDER BY rowid DESC")]

    def attempt(self, ident):
        row = self.db.execute("SELECT data FROM attempts WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        return json.loads(row[0])

    def latest_profile_result(self, profile_id, configured_model):
        row = self.db.execute(
            "SELECT json_extract(data,'$.status'), json_extract(data,'$.error_message') "
            "FROM attempts WHERE json_extract(data,'$.profile_id')=? "
            "AND json_extract(data,'$.configured_model')=? "
            "AND json_extract(data,'$.status') IN ('passed','failed','adapter_error') "
            "ORDER BY rowid DESC LIMIT 1",
            (profile_id, configured_model),
        ).fetchone()
        return tuple(row) if row else None

    def attempts(self, run_id, effective=True):
        rows = [
            json.loads(r[0])
            for r in self.db.execute("SELECT data FROM attempts WHERE run_id=? ORDER BY rowid", (run_id,))
        ]
        if effective:
            superseded = {r.get("supersedes_attempt_id") for r in rows}
            rows = [r for r in rows if r["id"] not in superseded]
        return rows

    def artifact_dir(self, run_id, attempt_id):
        path = safe_child(self.artifacts, Path(run_id) / attempt_id)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        (path.parent / ".routebench-owned").touch(mode=0o600)
        return path

    def register_artifact(self, attempt, kind, path, truncated=False):
        path = safe_child(self.artifacts, path)
        ident = f"{attempt['id']}-{kind}"
        data = {
            "id": ident,
            "kind": kind,
            "path": str(path.relative_to(self.artifacts)),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "byte_size": path.stat().st_size,
            "truncated": truncated,
        }
        with self.db:
            self.db.execute(
                "INSERT INTO artifacts VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                (ident, attempt["run_id"], attempt["id"], json.dumps(data)),
            )
        return data

    def artifact(self, ident):
        row = self.db.execute("SELECT data FROM artifacts WHERE id=?", (ident,)).fetchone()
        if row is None:
            raise KeyError(ident)
        data = json.loads(row[0])
        return data, safe_child(self.artifacts, data["path"])

    def delete(self, run_id):
        with self.db:
            self.db.execute("DELETE FROM runs WHERE id=?", (run_id,))

    def recover(self):
        for run in self.runs():
            attempts = self.attempts(run["id"])
            if run["status"] not in {"running", "queued"} and not any(
                a["status"] in {"queued", "preparing", "running", "grading"} for a in attempts
            ):
                continue
            for attempt in attempts:
                if attempt["status"] in {"preparing", "running", "grading"}:
                    attempt.update(
                        status="infrastructure_error",
                        score=None,
                        full_pass=None,
                        error_code="interrupted",
                        error_message="Backend stopped; explicitly rerun this attempt.",
                        completed_at=now(),
                    )
                    self.save_attempt(attempt, "attempt.error")
                elif attempt["status"] == "queued":
                    attempt.update(status="cancelled", error_code="restart", completed_at=now())
                    self.save_attempt(attempt, "attempt.status")
            run.update(status="completed_with_errors", completed_at=now())
            run["warnings"].append("Interrupted run recovered. No agent execution was resumed automatically.")
            self.save_run(run, "run.completed")
