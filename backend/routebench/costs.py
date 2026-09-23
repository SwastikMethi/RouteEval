"""Token-based list-price estimates and their saved provenance."""

import json
import math
from datetime import datetime, timezone
from decimal import Decimal


def _number(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def estimate_cost(result, pricing):
    if result.get("cost_usd") is not None or not pricing or result.get("usage_complete") is False:
        return
    incoming, outgoing = result.get("input_tokens"), result.get("output_tokens")
    cached, written = result.get("cached_input_tokens"), result.get("cache_write_tokens")
    if not all(_number(v) for v in (incoming, outgoing)):
        return
    if any(v is not None and not _number(v) for v in (cached, written)):
        return
    inclusive = result.get("input_includes_cache", False)
    if inclusive and (cached is None or cached > incoming):
        return
    amounts = {
        "input": (incoming - (cached or 0) if inclusive else incoming, "input_per_million"),
        "cached_input": (cached or 0, "cached_input_per_million"),
        "output": (outgoing, "output_per_million"),
    }
    if not inclusive and not cached:
        amounts.pop("cached_input")
    if written:
        amounts["cache_write"] = (written, "cache_write_per_million")
    if any(not _number(pricing.get(rate)) for _, rate in amounts.values()):
        return
    buckets = {}
    total = Decimal(0)
    for name, (tokens, rate_key) in amounts.items():
        rate = pricing[rate_key]
        cost = Decimal(str(tokens)) * Decimal(str(rate)) / 1_000_000
        total += cost
        buckets[name] = {"tokens": tokens, "rate_per_million": rate, "cost_usd": float(cost)}
    if not math.isfinite(float(total)):
        return
    result.update(
        cost_usd=float(total),
        cost_provenance="estimated",
        cost_estimate={
            "model": result.get("configured_model"),
            "pricing": dict(pricing),
            "calculated_at": datetime.now(timezone.utc).isoformat(),
            "buckets": buckets,
        },
    )


def cost_metadata(attempts):
    """Summarize only prices actually included in a total; missing values are coverage."""
    provenance, sources = set(), {}
    for attempt in attempts:
        kind = attempt.get("cost_provenance")
        if kind not in {"reported", "estimated"} or not _number(attempt.get("cost_usd")):
            continue
        provenance.add(kind)
        pricing = (attempt.get("cost_estimate") or {}).get("pricing")
        if kind == "estimated" and pricing:
            sources[json.dumps(pricing, sort_keys=True)] = pricing
    return {
        "cost_provenance": next(iter(provenance))
        if len(provenance) == 1
        else "mixed"
        if provenance
        else "unavailable",
        "cost_sources": list(sources.values()),
    }
