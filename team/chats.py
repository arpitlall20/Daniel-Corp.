"""Saved conversations: one JSON file per team member in data/chats/ (stays on your computer).

The full conversation is kept for you to read. Only the most recent messages are sent back to
Claude each time, which keeps every message cheap; lasting facts live in the shared team memory.
"""
from __future__ import annotations

import json
from pathlib import Path

CHATS = Path(__file__).resolve().parents[1] / "data" / "chats"
CONTEXT_MESSAGES = 20   # how many recent messages each agent sees (10 back-and-forths)


def _file(member: str) -> Path:
    return CHATS / f"{member}.json"


def load(member: str) -> list[dict]:
    try:
        return json.loads(_file(member).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save(member: str, transcript: list[dict]) -> None:
    CHATS.mkdir(parents=True, exist_ok=True)
    _file(member).write_text(json.dumps(transcript, indent=1, ensure_ascii=False), encoding="utf-8")


def clear(member: str) -> None:
    _file(member).unlink(missing_ok=True)


def model_history(transcript: list[dict]) -> list[dict]:
    """Recent messages in the format Claude expects; always starts with a user message."""
    recent = transcript[-CONTEXT_MESSAGES:]
    while recent and recent[0]["role"] != "you":
        recent = recent[1:]
    return [{"role": "user" if m["role"] == "you" else "assistant", "content": m["text"]} for m in recent]
