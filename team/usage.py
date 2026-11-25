"""Permanent spend ledger: every API call's estimated cost, kept in data/usage.json across restarts.

These are estimates from token counts and list prices. Your exact balance and invoices are in the
Claude Console (console.anthropic.com > Billing).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path

USAGE_FILE = Path(__file__).resolve().parents[1] / "data" / "usage.json"
_lock = threading.Lock()


def _empty() -> dict:
    return {"since": datetime.now().strftime("%Y-%m-%d"), "total": 0.0, "calls": 0,
            "by_agent": {}, "by_day": {}, "by_month": {}}


def load() -> dict:
    try:
        return json.loads(USAGE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return _empty()


def record(agent: str, cost: float) -> None:
    now = datetime.now()
    day, month = now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")
    with _lock:
        d = load()
        d["total"] = d.get("total", 0.0) + cost
        d["calls"] = d.get("calls", 0) + 1
        for bucket, key in (("by_agent", agent), ("by_day", day), ("by_month", month)):
            d.setdefault(bucket, {})
            d[bucket][key] = d[bucket].get(key, 0.0) + cost
        USAGE_FILE.parent.mkdir(exist_ok=True)
        tmp = USAGE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=1), encoding="utf-8")
        tmp.replace(USAGE_FILE)


def summary() -> dict:
    d = load()
    now = datetime.now()
    budget = float(os.getenv("MONTHLY_BUDGET_USD", "0") or 0)
    month = d.get("by_month", {}).get(now.strftime("%Y-%m"), 0.0)
    return {
        "today": round(d.get("by_day", {}).get(now.strftime("%Y-%m-%d"), 0.0), 4),
        "month": round(month, 4),
        "all_time": round(d.get("total", 0.0), 4),
        "calls": d.get("calls", 0),
        "since": d.get("since"),
        "budget": budget,
        "over_budget": bool(budget and month > budget),
        "by_agent": {k: round(v, 4) for k, v in sorted(d.get("by_agent", {}).items(), key=lambda x: -x[1])},
        "by_day": {k: round(v, 4) for k, v in sorted(d.get("by_day", {}).items())[-14:]},
        "by_month": {k: round(v, 4) for k, v in sorted(d.get("by_month", {}).items())},
    }
