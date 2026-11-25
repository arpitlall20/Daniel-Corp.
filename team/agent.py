"""The core agent loop: think -> call tools -> read results -> repeat.

Also tracks cost, a live status for every agent, and a step-by-step trace of every request ("run"),
which the web panel uses to show your team working in real time.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from . import usage
from .config import is_enabled
from .tools.base import S, Tool, schema

# USD per million tokens (input, output, cache read). Check platform.claude.com/docs/en/about-claude/pricing
PRICES = {
    "claude-opus-5-5": (4.00, 20.00, 0.20),
    "claude-sonnet-5-5": (2.00, 10.00, 0.10),
    "claude-haiku-5-5": (0.10, 0.50, 0.01),  # prompts up to 100K tokens
}
WEB_SEARCH_PRICE = 0.01  # $10 per 1,000 searches
COST_LOG: dict[str, float] = defaultdict(float)

# ---- live state shared with the web panel --------------------------------------------------
EVENTS: list[dict] = []                    # recent activity (also appended to data/activity.log)
STATUS: dict[str, dict] = {}               # agent key -> {state, doing, task, run, since}
RUNS: dict[str, dict] = {}                 # run id -> {cost, steps, started}
CANCELLED: set[str] = set()                # run ids you pressed Stop on
ACTIVITY_LOG = Path(__file__).resolve().parents[1] / "data" / "activity.log"
_lock = threading.Lock()
_ctx = threading.local()                   # which run / agent this thread is working for


class StopRequested(BaseException):
    """Raised inside a run when you press Stop. A BaseException so tool error handling can't swallow it."""


def begin_run(run_id: str | None) -> None:
    _ctx.run_id = run_id
    _ctx.stack = []
    if run_id:
        RUNS[run_id] = {"cost": 0.0, "steps": 0, "started": time.time()}


def current_run() -> str | None:
    return getattr(_ctx, "run_id", None)


def current_agent() -> str | None:
    stack = getattr(_ctx, "stack", [])
    return stack[-1] if stack else None


def set_status(key: str, state: str, doing: str = "", task: str | None = None) -> None:
    with _lock:
        prev = STATUS.get(key, {})
        STATUS[key] = {"state": state, "doing": doing, "since": time.time(), "run": current_run(),
                       "task": prev.get("task", "") if task is None else task}


def log_event(agent: str, text: str, kind: str = "info", key: str | None = None,
              target: str | None = None, detail: str = "") -> None:
    run = current_run()
    with _lock:
        ev = {"id": max(int(time.time() * 1000), (EVENTS[-1]["id"] + 1) if EVENTS else 0),
              "time": time.strftime("%H:%M:%S"), "date": time.strftime("%Y-%m-%d"), "ts": time.time(),
              "agent": agent, "text": text, "kind": kind, "key": key or current_agent(), "target": target,
              "detail": detail[:300], "run": run}
        EVENTS.append(ev)
        del EVENTS[:-1000]
        if run in RUNS and kind in ("tool", "search", "delegate"):
            RUNS[run]["steps"] += 1
    try:
        ACTIVITY_LOG.parent.mkdir(exist_ok=True)
        with ACTIVITY_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _check_stop() -> None:
    run = current_run()
    if run and run in CANCELLED:
        raise StopRequested()


def _summary(args: dict) -> str:
    for k in ("query", "title", "member", "path", "command", "start", "day", "name", "fact", "search", "status"):
        if args.get(k):
            return f"{args[k]}"[:90]
    return ""


_client = None


def client():
    global _client
    if _client is None:
        import anthropic  # lazy import

        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    return _client


def _cost(model: str, usage_obj) -> float:
    p_in, p_out, p_cache = PRICES.get(model, PRICES["claude-sonnet-5-5"])
    cache_read = getattr(usage_obj, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage_obj, "cache_creation_input_tokens", 0) or 0
    cost = (usage_obj.input_tokens * p_in + cache_write * p_in * 1.25 + cache_read * p_cache
            + usage_obj.output_tokens * p_out) / 1_000_000
    stu = getattr(usage_obj, "server_tool_use", None)
    if stu and getattr(stu, "web_search_requests", 0):
        cost += stu.web_search_requests * WEB_SEARCH_PRICE
    return cost


@dataclass
class Agent:
    key: str                     # e.g. "senior_dev"; used for on/off switches
    name: str
    model: str
    instructions: str
    tools: list[Tool] = field(default_factory=list)
    server_tools: list[dict] = field(default_factory=list)   # e.g. Claude's built-in web search
    team: dict[str, "Agent"] = field(default_factory=dict)  # agents this one can delegate to
    extra_context: callable = None                           # returns text appended to instructions
    max_turns: int = 25
    role: str = ""                                           # one-line job description
    icon: str = ""                                           # emoji shown in the panel
    custom: bool = False                                     # made in the panel with the + button

    def __post_init__(self):
        self.set_team(self.team)

    def set_team(self, team: dict[str, "Agent"]) -> None:
        """(Re)wire who this agent can delegate to. Called whenever you change the org chart."""
        self.team = dict(team)
        self.tools = [t for t in self.tools if t.name != "delegate"]
        if self.team:
            self.tools.append(Tool(
                "delegate",
                "Hand a task to someone in your team and get their report back. Your team: "
                + ", ".join(f"{k} ({a.name})" for k, a in self.team.items()),
                schema({"member": {"type": "string", "enum": list(self.team)}, "task": S}, ["member", "task"]),
                self._delegate,
            ))

    def team_brief(self) -> str:
        if not self.team:
            return ""
        lines = [f"- {k}: {a.name}, {a.role or 'team member'}" + ("" if is_enabled(k) else " (switched OFF)")
                 for k, a in self.team.items()]
        return "\n\nYour team (hand work over with `delegate`; pick the best fit for each part):\n" + "\n".join(lines)

    def _delegate(self, member: str, task: str) -> str:
        if member not in self.team:
            return f"Unknown team member {member}."
        if not is_enabled(member):
            log_event(self.name, f"couldn't delegate: {self.team[member].name} is switched off", "error",
                      key=self.key, target=member)
            return f"{member} is switched OFF by the owner. Tell the owner, or handle it another way."
        sub = self.team[member]
        print(f"  -> {self.name} delegates to {sub.name}: {task[:90]}")
        log_event(self.name, f"delegated to {sub.name}: {task[:200]}", "delegate", key=self.key, target=member,
                  detail=task)
        set_status(self.key, "delegating", f"Waiting on {sub.name}")
        try:
            result = sub.run(task)
        finally:
            set_status(self.key, "working", "Reviewing the report")
        log_event(sub.name, f"reported back to {self.name}: {result[:400]}", "report", key=member, target=self.key,
                  detail=result)
        return result

    def run(self, task: str, history: list | None = None) -> str:
        if not is_enabled(self.key):
            return f"{self.name} is switched off."
        if not hasattr(_ctx, "stack"):
            begin_run(None)
        _ctx.stack.append(self.key)
        set_status(self.key, "working", "Reading the request", task=task[:200])
        log_event(self.name, f"started: {task[:200]}", "start", key=self.key, detail=task)
        try:
            reply = self._loop(task, history)
            log_event(self.name, "finished", "done", key=self.key, detail=reply)
            set_status(self.key, "idle", "", task="")
            return reply
        except StopRequested:
            set_status(self.key, "idle", "", task="")
            log_event(self.name, "stopped by you", "stopped", key=self.key)
            raise
        except Exception as e:
            set_status(self.key, "error", f"{type(e).__name__}: {str(e)[:160]}")
            log_event(self.name, f"crashed: {type(e).__name__}: {e}", "error", key=self.key)
            raise
        finally:
            _ctx.stack.pop()

    def _loop(self, task: str, history: list | None) -> str:
        system = (self.instructions + (("\n\n" + self.extra_context()) if self.extra_context else "")
                  + self.team_brief())
        system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        tool_specs = [t.spec() for t in self.tools] + self.server_tools
        tools_by_name = {t.name: t for t in self.tools}
        messages = (history or []) + [{"role": "user", "content": task}]

        for _ in range(self.max_turns):
            _check_stop()
            set_status(self.key, "working", "Thinking…")
            kwargs = {"tools": tool_specs} if tool_specs else {}
            resp = client().messages.create(model=self.model, max_tokens=8000, system=system_blocks,
                                            messages=messages, **kwargs)
            call_cost = _cost(self.model, resp.usage)
            COST_LOG[self.name] += call_cost
            usage.record(self.name, call_cost)
            if current_run() in RUNS:
                RUNS[current_run()]["cost"] += call_cost
            messages.append({"role": "assistant", "content": resp.content})
            for block in resp.content:
                if block.type == "server_tool_use":
                    q = (block.input or {}).get("query", "")
                    log_event(self.name, f"web search: {q}", "search", key=self.key, detail=q)

            if resp.stop_reason == "tool_use":
                results = []
                for block in resp.content:
                    if block.type != "tool_use":
                        continue
                    _check_stop()
                    if os.getenv("VERBOSE") == "1":
                        print(f"    [{self.name}] {block.name}({str(block.input)[:120]})")
                    if block.name != "delegate":
                        set_status(self.key, "working", f"tool:{block.name}")
                        log_event(self.name, f"using {block.name}", "tool", key=self.key,
                                  detail=f"{block.name}|{_summary(block.input or {})}")
                    tool = tools_by_name.get(block.name)
                    out = tool.run(**block.input) if tool else f"Unknown tool {block.name}"
                    if out.startswith("ERROR"):
                        log_event(self.name, f"{block.name} FAILED: {out[:400]}", "error", key=self.key)
                    elif out.startswith("Owner declined"):
                        log_event(self.name, f"{block.name}: you declined", "declined", key=self.key)
                    results.append({"type": "tool_result", "tool_use_id": block.id, "content": out})
                messages.append({"role": "user", "content": results})
            elif resp.stop_reason == "pause_turn":   # long server-side search; just continue
                continue
            else:
                if history is not None:
                    history[:] = messages
                return "".join(b.text for b in resp.content if b.type == "text")
        return f"{self.name} stopped after {self.max_turns} steps without finishing."


def cost_report() -> str:
    if not COST_LOG:
        return "No API spend this session."
    lines = [f"  {name:<24} ${c:.4f}" for name, c in COST_LOG.items()]
    u = usage.summary()
    return ("Session cost (USD, estimate):\n" + "\n".join(lines) + f"\n  {'TOTAL':<24} ${sum(COST_LOG.values()):.4f}"
            f"\nToday ${u['today']:.4f} | This month ${u['month']:.4f} | All time ${u['all_time']:.4f} (since {u['since']})")
