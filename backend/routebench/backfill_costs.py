"""Offline, idempotent enrichment of saved Codex attempts; never launches a model."""

import hashlib
import json
import sqlite3
import uuid
from collections import Counter

from .adapters import AdapterParser
from .costs import estimate_cost
from .storage import Store

USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_tokens", "output_tokens")


def backfill_costs(settings, *, apply=False):
    store = Store(settings.database, settings.artifacts)
    try:
        runs = store.runs()
        if any(run["status"] in {"queued", "running"} for run in runs):
            raise ValueError("Finish or recover active runs before backfilling cost estimates")
        prices = {}
        for profile in settings.profiles.values():
            if profile["adapter"] == "codex" and profile.get("pricing"):
                model, pricing = profile["model"], profile["pricing"]
                if model in prices and prices[model] != pricing:
                    raise ValueError("Conflicting prices for the same Codex model")
                prices[model] = pricing
        changes, skipped = [], Counter()
        for run in runs:
            profiles = {p["id"]: p for p in run["profiles"]}
            for attempt in store.attempts(run["id"], effective=False):
                if profiles[attempt["profile_id"]]["adapter"] != "codex":
                    continue
                if attempt.get("cost_usd") is not None:
                    skipped["existing_cost"] += 1
                    continue
                model = attempt.get("configured_model")
                if model not in prices or attempt["status"] not in {"passed", "failed", "adapter_error"}:
                    skipped["no_supported_model_or_completed_usage"] += 1
                    continue
                candidate = dict(attempt)
                if candidate.get("usage_complete") is not True:
                    artifact = next((a for a in attempt.get("artifacts", []) if a["kind"] == "stdout"), None)
                    try:
                        if not artifact:
                            raise ValueError("No usage artifact")
                        meta, path = store.artifact(artifact["id"])
                        if meta["truncated"] or path.stat().st_size != meta["byte_size"]:
                            raise ValueError("Incomplete usage artifact")
                        content = path.read_bytes()
                        if hashlib.sha256(content).hexdigest() != meta["sha256"]:
                            raise ValueError("Usage artifact changed")
                        parser = AdapterParser("codex", model)
                        for line in content.decode().splitlines():
                            parser.feed(line)
                        parsed = parser.finalize(attempt.get("exit_code"))
                        if not parsed["usage_complete"]:
                            raise ValueError("Incomplete token usage")
                        if any(attempt.get(k) is not None and attempt[k] != parsed[k] for k in USAGE_FIELDS):
                            raise ValueError("Saved token counts disagree with artifact")
                        candidate.update({k: parsed[k] for k in USAGE_FIELDS})
                        candidate.update(usage_complete=True, input_includes_cache=True)
                    except (OSError, ValueError, KeyError):
                        skipped["missing_or_invalid_usage_evidence"] += 1
                        continue
                estimate_cost(candidate, prices[model])
                if candidate.get("cost_provenance") != "estimated":
                    skipped["incomplete_usage_or_rates"] += 1
                    continue
                candidate["cost_estimate"]["backfilled"] = True
                changes.append(candidate)
        report = {
            "applied": apply,
            "eligible": len(changes),
            "skipped": dict(skipped),
            "attempts": [
                {"id": a["id"], "model": a["configured_model"], "cost_usd": a["cost_usd"]} for a in changes
            ],
            "backup": None,
        }
        if apply and changes:
            backup = settings.database.with_name(
                f"{settings.database.stem}.before-costs-{uuid.uuid4().hex[:12]}.db"
            )
            backup.touch(mode=0o600, exist_ok=False)
            target = sqlite3.connect(backup)
            try:
                store.db.backup(target)
            finally:
                target.close()
            report["backup"] = str(backup)
            with store.db:
                for attempt in changes:
                    store.db.execute(
                        "UPDATE attempts SET data=? WHERE id=?", (json.dumps(attempt), attempt["id"])
                    )
                    store._event(
                        attempt["run_id"],
                        "attempt.cost_estimated",
                        {
                            "attempt_id": attempt["id"],
                            "cost_usd": attempt["cost_usd"],
                            "pricing": attempt["cost_estimate"]["pricing"],
                        },
                    )
        return report
    finally:
        store.close()
