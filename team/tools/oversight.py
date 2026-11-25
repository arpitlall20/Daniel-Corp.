"""Lets the team lead see what the team has been doing, so it can investigate instead of asking you."""
from __future__ import annotations

import json

from .. import chats
from ..agent import ACTIVITY_LOG
from .base import I, S, Tool, schema

NAMES = {"assistant": "calendar", "travel": "travel", "senior_dev": "senior", "junior_dev": "junior",
         "team_lead": "team lead"}


def read_team_activity(member: str = "", last_n: int = 40, only_problems: bool = False) -> str:
    try:
        lines = ACTIVITY_LOG.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return "No activity recorded yet."
    events = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if member:
        key = NAMES.get(member, member).lower()
        events = [e for e in events if key in e["agent"].lower() or key in e["text"].lower()]
    if only_problems:
        events = [e for e in events if "FAILED" in e["text"] or "Error" in e["text"] or "declined" in e["text"]]
    events = events[-max(1, min(last_n, 200)):]
    return "\n".join(f"{e.get('date', '')} {e['time']} | {e['agent']}: {e['text']}" for e in events) or "Nothing matching."


def read_member_chat(member: str, last_n: int = 10) -> str:
    transcript = chats.load(member)[-max(1, min(last_n, 50)):]
    if not transcript:
        return f"The owner has no saved direct chat with {member}."
    return "\n\n".join(f"{'OWNER' if m['role'] == 'you' else member.upper()}: {m['text'][:1500]}" for m in transcript)


MEMBER = {"type": "string", "description": "assistant, travel, senior_dev or junior_dev"}

OVERSIGHT_TOOLS = [
    Tool("read_team_activity",
         "Read the team's activity log: delegations, tools used, errors (FAILED lines), approvals and reports. "
         "Filter by member and/or only_problems. Use this FIRST when the owner reports something went wrong.",
         schema({"member": S, "last_n": I, "only_problems": {"type": "boolean"}}), read_team_activity),
    Tool("read_member_chat",
         "Read the owner's recent direct conversation with a team member, to see exactly what was asked and answered.",
         schema({"member": MEMBER, "last_n": I}, ["member"]), read_member_chat),
]
