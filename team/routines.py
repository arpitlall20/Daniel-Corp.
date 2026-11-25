"""Routines: jobs your team does on a schedule ("every weekday at 7:30, give me a morning briefing").

Saved in data/routines.json. They run while the panel (web.py) is running; turn on "Start with
Windows" in the Routines tab so that's always the case.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .tools.base import I, S, Tool, schema

FILE = Path(__file__).resolve().parents[1] / "data" / "routines.json"
TZ = ZoneInfo(os.getenv("TIMEZONE", "Australia/Brisbane"))
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
TYPES = ("daily", "weekdays", "days", "hours", "monthly", "once")
MISSED_GRACE = timedelta(minutes=30)   # later than this counts as "missed while the panel was off"
_lock = threading.RLock()


def now() -> datetime:
    return datetime.now(TZ)


def _load() -> list[dict]:
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save(items: list[dict]) -> None:
    FILE.parent.mkdir(exist_ok=True)
    tmp = FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(items, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(FILE)


def _at(d: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in (hhmm or "08:00").split(":")[:2])
    return d.replace(hour=h, minute=m, second=0, microsecond=0)


def next_run(s: dict, after: datetime) -> datetime | None:
    """The first time this schedule fires strictly after `after`."""
    kind, t = s.get("type", "daily"), s.get("time", "08:00")
    if kind == "once":
        when = datetime.fromisoformat(s["date"] + "T" + t).replace(tzinfo=TZ)
        return when if when > after else None
    if kind == "hours":
        n = max(1, int(s.get("every", 1)))
        start = _at(after, t)
        while start > after:
            start -= timedelta(hours=n)
        while start <= after:
            start += timedelta(hours=n)
        return start
    if kind == "monthly":
        day = min(max(1, int(s.get("day", 1))), 28)
        d = after
        for _ in range(3):
            cand = _at(d.replace(day=day), t)
            if cand > after:
                return cand
            d = (d.replace(day=1) + timedelta(days=32)).replace(day=1)
        return None
    days = {"daily": range(7), "weekdays": range(5)}.get(kind) or [int(x) for x in s.get("days", [])] or range(7)
    for i in range(8):
        cand = _at(after + timedelta(days=i), t)
        if cand > after and cand.weekday() in days:
            return cand
    return None


def describe(s: dict) -> str:
    t = datetime.strptime(s.get("time", "08:00"), "%H:%M").strftime("%I:%M %p").lstrip("0").lower()
    kind = s.get("type")
    if kind == "daily":
        return f"Every day at {t}"
    if kind == "weekdays":
        return f"Every weekday at {t}"
    if kind == "days":
        days = [DAY_NAMES[int(d)] for d in sorted(s.get("days", []))]
        return f"Every {', '.join(days) or 'day'} at {t}"
    if kind == "hours":
        n = int(s.get("every", 1))
        return "Every hour" if n == 1 else f"Every {n} hours"
    if kind == "monthly":
        d = int(s.get("day", 1))
        suffix = "th" if 10 <= d % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(d % 10, "th")
        return f"Monthly on the {d}{suffix} at {t}"
    if kind == "once":
        return f"Once on {s.get('date')} at {t}"
    return "Custom"


def _clean(spec: dict) -> dict:
    sched = spec.get("schedule") or {}
    kind = sched.get("type") if sched.get("type") in TYPES else "daily"
    clean_s = {"type": kind, "time": (sched.get("time") or "08:00")[:5]}
    if kind == "days":
        clean_s["days"] = sorted({int(d) for d in sched.get("days", []) if 0 <= int(d) <= 6}) or [0]
    if kind == "hours":
        clean_s["every"] = min(max(1, int(sched.get("every") or 1)), 24)
    if kind == "monthly":
        clean_s["day"] = min(max(1, int(sched.get("day") or 1)), 28)
    if kind == "once":
        clean_s["date"] = sched.get("date") or now().strftime("%Y-%m-%d")
    name = (spec.get("name") or "").strip()[:60]
    prompt = (spec.get("prompt") or "").strip()[:4000]
    if not name or not prompt:
        raise ValueError("Give the routine a name and say what to do.")
    datetime.strptime(clean_s["time"], "%H:%M")  # validates the time
    return {"name": name, "agent": spec.get("agent") or "team_lead", "prompt": prompt, "schedule": clean_s,
            "enabled": bool(spec.get("enabled", True)), "catch_up": bool(spec.get("catch_up", True)),
            "notify": bool(spec.get("notify", True))}


def _with_next(r: dict, after: datetime | None = None) -> dict:
    nxt = next_run(r["schedule"], after or now())
    r["next_run"] = nxt.isoformat() if nxt else None
    if nxt is None and r["schedule"]["type"] == "once":
        r["enabled"] = False
    return r


def all_routines() -> list[dict]:
    with _lock:
        items = _load()
        for r in items:
            r["describe"] = describe(r["schedule"])
        return items


def get(rid: str) -> dict | None:
    return next((r for r in _load() if r["id"] == rid), None)


def create(spec: dict) -> dict:
    with _lock:
        r = {**_clean(spec), "id": uuid.uuid4().hex[:8], "created": now().isoformat(timespec="minutes"),
             "last_run": None, "last_status": None, "history": []}
        items = _load()
        items.append(_with_next(r))
        _save(items)
        return r


def update(rid: str, spec: dict) -> dict:
    with _lock:
        items = _load()
        for r in items:
            if r["id"] == rid:
                r.update(_clean({**r, **spec}))
                _with_next(r)
                _save(items)
                return r
        raise ValueError("Routine not found.")


def set_enabled(rid: str, on: bool) -> dict:
    with _lock:
        items = _load()
        for r in items:
            if r["id"] == rid:
                r["enabled"] = on
                _with_next(r)
                _save(items)
                return r
        raise ValueError("Routine not found.")


def delete(rid: str) -> str:
    with _lock:
        items = _load()
        keep = [r for r in items if r["id"] != rid]
        if len(keep) == len(items):
            raise ValueError("Routine not found.")
        _save(keep)
        return next(r["name"] for r in items if r["id"] == rid)


def due(busy_agents: set[str]) -> list[tuple[dict, str]]:
    """Routines to run now, as (routine, reason). Missed ones (panel was off) run once if catch_up is on."""
    out, t = [], now()
    with _lock:
        items, changed = _load(), False
        for r in items:
            if not r.get("enabled") or not r.get("next_run"):
                continue
            when = datetime.fromisoformat(r["next_run"])
            if when > t or r["agent"] in busy_agents:
                continue
            missed = t - when > MISSED_GRACE
            if missed and not r.get("catch_up", True):
                r["last_status"] = "skipped (panel was off)"
                _with_next(r, t)
                changed = True
                continue
            r["last_run"] = t.isoformat(timespec="seconds")
            r["last_status"] = "running"
            _with_next(r, t)
            changed = True
            out.append((dict(r), "catching up on a missed run" if missed else "on schedule"))
        if changed:
            _save(items)
    return out


def mark_started(rid: str) -> None:
    with _lock:
        items = _load()
        for r in items:
            if r["id"] == rid:
                r["last_run"] = now().isoformat(timespec="seconds")
                r["last_status"] = "running"
        _save(items)


def record(rid: str, status: str, reply: str, run: str, secs: float, cost: float) -> None:
    with _lock:
        items = _load()
        for r in items:
            if r["id"] == rid:
                r["last_status"] = status
                r.setdefault("history", []).insert(0, {"ts": now().isoformat(timespec="seconds"), "status": status,
                                                       "reply": reply[:6000], "run": run, "secs": secs, "cost": cost})
                r["history"] = r["history"][:15]
        _save(items)


# ---- tools so the team lead can set routines up when you just ask ------------------------
def _tool_create(name: str, agent: str, instructions: str, schedule_type: str, time: str = "08:00",
                 days: list[int] | None = None, every_hours: int = 0, day_of_month: int = 0, date: str = "") -> str:
    from .org import current
    if agent not in current():
        return f"Unknown team member '{agent}'. Choose from: {', '.join(current())}"
    r = create({"name": name, "agent": agent, "prompt": instructions,
                "schedule": {"type": schedule_type, "time": time, "days": days or [], "every": every_hours,
                             "day": day_of_month, "date": date}})
    return (f"Routine '{r['name']}' created: {describe(r['schedule'])}, done by {agent}. "
            f"Next run: {r['next_run']}. The owner can pause, edit or delete it in the Routines tab.")


def _tool_list() -> str:
    items = all_routines()
    return "\n".join(f"- {r['name']} ({r['describe']}, by {r['agent']}, {'on' if r['enabled'] else 'paused'}, "
                     f"next {r.get('next_run') or '-'})" for r in items) or "No routines yet."


ROUTINE_TOOLS = [
    Tool("create_routine",
         "Set up a recurring job when the owner asks for something on a schedule ('every morning...', 'each Monday...'). "
         "schedule_type: daily, weekdays, days (with days: 0=Mon..6=Sun), hours (with every_hours), monthly "
         "(with day_of_month), once (with date YYYY-MM-DD). time is 24h HH:MM in the owner's timezone. "
         "instructions = exactly what the agent should do each time, written as a complete request.",
         schema({"name": S, "agent": {"type": "string", "description": "team member key, e.g. team_lead or assistant"},
                 "instructions": S, "schedule_type": {"type": "string", "enum": list(TYPES)}, "time": S,
                 "days": {"type": "array", "items": I}, "every_hours": I, "day_of_month": I, "date": S},
                ["name", "agent", "instructions", "schedule_type"]), _tool_create),
    Tool("list_routines", "List the team's scheduled routines.", schema({}), _tool_list),
]
