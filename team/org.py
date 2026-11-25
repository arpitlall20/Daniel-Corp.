"""Your org chart: who reports to whom, and the team members you add with the + button.

Saved in data/org.json on your computer, so your changes survive restarts and updates.
"""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from . import config
from .agent import Agent
from .roster import COMMON, DEFAULT_PARENTS, WEB_SEARCH, build_builtin, now_context
from .tools.calendar import CALENDAR_TOOLS
from .tools.code import DEV_TOOLS
from .tools.memory import MEMORY_TOOLS, TRAVEL_MEMORY_TOOLS
from .tools.oversight import OVERSIGHT_TOOLS
from .tools.tasks import PROJECT_TOOLS, TASK_TOOLS
from .tools.wordpress import WORDPRESS_TOOLS

ORG_FILE = Path(__file__).resolve().parents[1] / "data" / "org.json"
_lock = threading.RLock()
_agents: dict[str, Agent] = {}

# What you can give a new team member. Team memory is always included.
ABILITIES = {
    "web_search":    ("🔎", "Search the web", "Look up current facts, prices and news", lambda: [], True),
    "tasks":         ("📋", "Task board & projects", "Read and update your tasks and projects", lambda: TASK_TOOLS + PROJECT_TOOLS, False),
    "calendar_read": ("📅", "Read your calendar", "See events and find free time", lambda: [t for t in CALENDAR_TOOLS if t.name in ("list_events", "find_free_slots")], False),
    "calendar_edit": ("🗓️", "Add & remove events", "Changes your calendar (asks you first)", lambda: [t for t in CALENDAR_TOOLS if t.name in ("create_event", "delete_event")], False),
    "wordpress":     ("🌐", "WordPress", "Write drafts; publishing asks you first", lambda: WORDPRESS_TOOLS, False),
    "code":          ("🧑‍💻", "Code & GitHub", "Edit code on branches and open pull requests", lambda: list(DEV_TOOLS), False),
    "travel_prefs":  ("🧳", "Travel preferences", "Read and save your travel preferences", lambda: TRAVEL_MEMORY_TOOLS, False),
    "oversight":     ("🕵️", "See team activity", "Read the activity log and team chats (good for managers)", lambda: OVERSIGHT_TOOLS, False),
}
MODELS = ["claude-sonnet-5-5", "claude-haiku-5-5", "claude-opus-5-5"]


def _load() -> dict:
    try:
        d = json.loads(ORG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    d.setdefault("parents", {})
    d.setdefault("custom", {})
    return d


def _save(d: dict) -> None:
    ORG_FILE.parent.mkdir(exist_ok=True)
    tmp = ORG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ORG_FILE)


def _custom_agent(spec: dict) -> Agent:
    tools, server = list(MEMORY_TOOLS), []
    for ab in spec.get("abilities", []):
        if ab not in ABILITIES:
            continue
        if ABILITIES[ab][4]:
            server.append(WEB_SEARCH)
        tools += ABILITIES[ab][3]()
    seen, unique = set(), []
    for t in tools:  # no duplicate tool names
        if t.name not in seen:
            seen.add(t.name)
            unique.append(t)
    instructions = COMMON + f"""
Role: {spec['name']}, {spec.get('role') or 'team member'}.
What you do, in the owner's words:
{spec.get('instructions') or 'Help with tasks in your area.'}

Work within the abilities you have. If a request needs something you can't do, say so plainly and
suggest who in the team could. Report back briefly: what you did, what you found, what needs approval."""
    return Agent(key=spec["key"], name=spec["name"], model=config.model_for(spec["key"]), instructions=instructions,
                 tools=unique, server_tools=server, extra_context=now_context, role=spec.get("role", ""),
                 icon=spec.get("icon", "🙂"), custom=True)


def parents() -> dict[str, str]:
    """Effective reporting lines: defaults, then your changes. Anyone unplaced reports to the team lead."""
    d = _load()
    out = dict(DEFAULT_PARENTS)
    for k, spec in d["custom"].items():
        out[k] = spec.get("reports_to", "team_lead")
    out.update(d["parents"])
    keys = set(_agents) or set(out)
    for k in list(out):
        if k not in keys:
            out.pop(k)
        elif out[k] != "you" and out[k] not in keys:
            out[k] = "team_lead"
    out["team_lead"] = "you"
    return out


def _wire() -> None:
    p = parents()
    for key, agent in _agents.items():
        agent.set_team({k: _agents[k] for k, boss in p.items() if boss == key and k in _agents})


def build_all() -> dict[str, Agent]:
    """Build every agent (built-in + yours) and wire up the org chart."""
    with _lock:
        agents = build_builtin()
        for key, spec in _load()["custom"].items():
            config.ensure_agent(key, spec.get("model", "claude-sonnet-5-5"))
            agents[key] = _custom_agent(spec)
        _agents.clear()
        _agents.update(agents)
        _wire()
        return _agents


def current() -> dict[str, Agent]:
    return _agents


def ordered_keys() -> list[str]:
    """Agents in org-chart order (depth-first from you)."""
    p, out = parents(), []

    def walk(boss):
        for k in [k for k in _agents if p.get(k) == boss]:
            out.append(k)
            walk(k)
    walk("you")
    return out + [k for k in _agents if k not in out]


def _descendants(key: str) -> set[str]:
    p, found, frontier = parents(), set(), {key}
    while frontier:
        nxt = {k for k, b in p.items() if b in frontier and k not in found}
        found |= nxt
        frontier = nxt
    return found


def set_parent(key: str, boss: str) -> str:
    with _lock:
        if key not in _agents:
            raise ValueError("Unknown team member.")
        if key == "team_lead":
            raise ValueError("The Team Lead always reports straight to you.")
        if boss != "you" and boss not in _agents:
            raise ValueError("Unknown manager.")
        if boss == key or boss in _descendants(key):
            raise ValueError(f"{_agents[key].name} can't report to someone in their own team.")
        d = _load()
        d["parents"][key] = boss
        _save(d)
        _wire()
        return f"{_agents[key].name} now reports to {'you' if boss == 'you' else _agents[boss].name}."


def _slug(name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "agent"
    key, n = base, 2
    while key in _agents or key in ("you", "all"):
        key, n = f"{base}_{n}", n + 1
    return key


def _clean(spec: dict) -> dict:
    name = (spec.get("name") or "").strip()[:40]
    if not name:
        raise ValueError("Give your new team member a name.")
    model = spec.get("model") if spec.get("model") in MODELS else "claude-sonnet-5-5"
    return {"name": name, "icon": (spec.get("icon") or "🙂").strip()[:4], "role": (spec.get("role") or "").strip()[:80],
            "instructions": (spec.get("instructions") or "").strip()[:4000], "model": model,
            "abilities": [a for a in spec.get("abilities", []) if a in ABILITIES]}


def add_agent(spec: dict) -> str:
    with _lock:
        clean = _clean(spec)
        key = _slug(clean["name"])
        boss = spec.get("reports_to") or "team_lead"
        if boss != "you" and boss not in _agents:
            boss = "team_lead"
        d = _load()
        d["custom"][key] = {**clean, "key": key, "reports_to": boss, "created": time.strftime("%Y-%m-%d %H:%M")}
        d["parents"][key] = boss
        _save(d)
        config.ensure_agent(key, clean["model"])
        config.set_model(key, clean["model"])
        _agents[key] = _custom_agent(d["custom"][key])
        _wire()
        return key


def update_agent(key: str, spec: dict) -> None:
    with _lock:
        d = _load()
        if key not in d["custom"]:
            raise ValueError("Only team members you added can be edited here.")
        clean = _clean(spec)
        d["custom"][key].update(clean)
        _save(d)
        config.set_model(key, clean["model"])
        _agents[key] = _custom_agent(d["custom"][key])
        if spec.get("reports_to"):
            set_parent(key, spec["reports_to"])
        _wire()


def remove_agent(key: str) -> str:
    with _lock:
        d = _load()
        if key not in d["custom"]:
            raise ValueError("Built-in team members can't be removed, but you can switch them off.")
        boss = parents().get(key, "team_lead")
        for k, b in list(parents().items()):     # their team moves up to their manager
            if b == key:
                d["parents"][k] = boss
        name = d["custom"].pop(key)["name"]
        d["parents"].pop(key, None)
        _save(d)
        config.remove_agent(key)
        _agents.pop(key, None)
        _wire()
        return f"{name} has left the team."


def spec_of(key: str) -> dict | None:
    s = _load()["custom"].get(key)
    return {**s, "reports_to": parents().get(key, "team_lead")} if s else None


def abilities_catalog() -> list[dict]:
    return [{"id": k, "icon": v[0], "label": v[1], "desc": v[2]} for k, v in ABILITIES.items()]
