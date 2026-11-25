"""The core agent loop: think -> call tools -> read results -> repeat. Also tracks cost."""
from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field

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
EVENTS: list[dict] = []  # live activity feed (shown in the web panel)


def log_event(agent: str, text: str) -> None:
    import time
    EVENTS.append({"id": len(EVENTS) + 1, "time": time.strftime("%H:%M:%S"), "agent": agent, "text": text})
    del EVENTS[:-500]

_client = None


def client():
    global _client
    if _client is None:
        import anthropic  # lazy import

        _client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
    return _client


def _cost(model: str, usage) -> float:
    p_in, p_out, p_cache = PRICES.get(model, PRICES["claude-sonnet-5-5"])
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cost = (usage.input_tokens * p_in + cache_write * p_in * 1.25 + cache_read * p_cache
            + usage.output_tokens * p_out) / 1_000_000
    stu = getattr(usage, "server_tool_use", None)
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

    def __post_init__(self):
        if self.team:
            self.tools.append(Tool(
                "delegate",
                "Hand a task to a team member and get their report back. Team members: "
                + ", ".join(f"{k} ({a.name})" for k, a in self.team.items()),
                schema({"member": {"type": "string", "enum": list(self.team)}, "task": S}, ["member", "task"]),
                self._delegate,
            ))

    def _delegate(self, member: str, task: str) -> str:
        if not is_enabled(member):
            return f"{member} is switched OFF by the owner. Tell the owner, or handle it another way."
        print(f"  -> {self.name} delegates to {self.team[member].name}: {task[:90]}")
        log_event(self.name, f"delegated to {self.team[member].name}: {task[:140]}")
        result = self.team[member].run(task)
        log_event(self.team[member].name, f"reported back to {self.name}")
        return result

    def run(self, task: str, history: list | None = None) -> str:
        if not is_enabled(self.key):
            return f"{self.name} is switched off."
        system = self.instructions + (("\n\n" + self.extra_context()) if self.extra_context else "")
        system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        tool_specs = [t.spec() for t in self.tools] + self.server_tools
        tools_by_name = {t.name: t for t in self.tools}
        messages = (history or []) + [{"role": "user", "content": task}]

        for _ in range(self.max_turns):
            kwargs = {"tools": tool_specs} if tool_specs else {}
            resp = client().messages.create(model=self.model, max_tokens=8000, system=system_blocks,
                                            messages=messages, **kwargs)
            COST_LOG[self.name] += _cost(self.model, resp.usage)
            messages.append({"role": "assistant", "content": resp.content})
            for block in resp.content:
                if block.type == "server_tool_use":
                    log_event(self.name, f"web search: {block.input.get('query', '')}")

            if resp.stop_reason == "tool_use":
                results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        if os.getenv("VERBOSE") == "1":
                            print(f"    [{self.name}] {block.name}({str(block.input)[:120]})")
                        if block.name != "delegate":
                            log_event(self.name, f"using {block.name}")
                        tool = tools_by_name.get(block.name)
                        out = tool.run(**block.input) if tool else f"Unknown tool {block.name}"
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
    return "Session cost (USD, estimate):\n" + "\n".join(lines) + f"\n  {'TOTAL':<24} ${sum(COST_LOG.values()):.4f}"
