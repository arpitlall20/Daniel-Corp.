"""Team meetings: you and your agents in one live conversation.

How a turn works:
1. You say something (voice or typing).
2. A quick "moderator" check (cheap Haiku call) decides who has something relevant to say: they
   raise their hands, in order, and anyone can react with an emoji. @mentioned people go first.
3. People speak one at a time from the hand queue. Each one is the real agent with its real tools,
   so they can check your calendar, tasks or the web before answering.
4. After the queue empties, one follow-up check lets someone respond to what was just said.
You can interrupt at any time (raise your hand or just speak), call on someone, mute, hold the floor.
"Wrap up" writes minutes, puts action items on the task board and saves the meeting.
"""
from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
import uuid
from pathlib import Path

from . import agent as core
from . import usage
from .tools.tasks import add_task

OWNER = os.getenv("OWNER_NAME", "the owner")
MEETINGS_DIR = Path(__file__).resolve().parents[1] / "data" / "meetings"
MODERATOR_MODEL = "claude-haiku-5-5"
MAX_TURNS_PER_ROUND = 6
EMOJIS = ["👍", "❤️", "😂", "👏", "🎉", "🤔", "✅", "😮", "🙌", "💡"]


def _plain(text: str) -> str:
    """Make a reply sound right when read aloud: no markdown symbols."""
    t = re.sub(r"```.*?```", "", text, flags=re.S)
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    t = re.sub(r"^\s*#+\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*•]\s+", "", t, flags=re.M)
    t = t.replace("**", "").replace("__", "").replace("`", "")
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def _json_from(text: str) -> dict:
    m = re.search(r"\{.*\}", text, flags=re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return {}


class Meeting:
    def __init__(self, agents: dict, title: str, agenda: list[str], attendees: list[str]):
        self.id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        self.agents = agents
        self.title = title.strip() or "Team meeting"
        self.agenda = [{"text": a.strip(), "done": False} for a in agenda if a.strip()][:15]
        self.current = 0 if self.agenda else -1
        self.attendees = [k for k in attendees if k in agents]
        self.muted: set[str] = set()
        self.hands: list[str] = []
        self.hand_why: dict[str, str] = {}
        self.you_hand = False
        self.hold = False
        self.thinking: str | None = None
        self.status = "live"
        self.started = time.time()
        self.ended: float | None = None
        self.entries: list[dict] = []
        self.minutes: dict | None = None
        self.cost = 0.0
        self.lock = threading.RLock()
        self.inbox: queue.Queue = queue.Queue()
        self._eid = 0
        self.add("system", f"Meeting started: {self.title}")
        threading.Thread(target=self._loop, daemon=True).start()

    # ---- transcript -----------------------------------------------------------------------
    def add(self, speaker: str, text: str, kind: str = "system", **extra) -> dict:
        with self.lock:
            self._eid += 1
            e = {"id": self._eid, "ts": time.time(), "speaker": speaker, "text": text, "kind": kind, **extra}
            self.entries.append(e)
            return e

    def name(self, key: str) -> str:
        if key == "you":
            return "You"
        a = self.agents.get(key)
        return (a.person or a.name) if a else key

    def full(self, key: str) -> str:
        a = self.agents.get(key)
        return a.display if a else key

    def _transcript(self, last: int = 30) -> str:
        lines = []
        for e in self.entries[-last:]:
            if e["kind"] == "speech":
                who = OWNER + " (the owner)" if e["speaker"] == "you" else self.full(e["speaker"])
                lines.append(f"{who}: {e['text']}")
            elif e["kind"] == "system":
                lines.append(f"[{e['text']}]")
        return "\n".join(lines)

    def _agenda_text(self) -> str:
        if not self.agenda:
            return "No fixed agenda."
        return "\n".join(f"{'✓' if a['done'] else ('→' if i == self.current else ' ')} {i + 1}. {a['text']}"
                         for i, a in enumerate(self.agenda))

    def _present(self) -> list[str]:
        return [k for k in self.attendees if k not in self.muted]

    # ---- owner actions (called from the web server) ---------------------------------------------
    def say(self, text: str) -> None:
        text = text.strip()
        if not text or self.status != "live":
            return
        with self.lock:
            self.you_hand = False
            self.hands.clear()  # people re-raise if still relevant
        self.add("you", text, "speech")
        self.inbox.put(("user", text))

    def react(self, who: str, emoji: str) -> None:
        if emoji in EMOJIS:
            self.add(who, emoji, "reaction")

    def raise_hand(self, on: bool) -> None:
        with self.lock:
            self.you_hand = on
        self.add("you", "raised their hand" if on else "lowered their hand", "hand")
        if on:
            self.add("system", "The floor is yours. The team will wait after the current speaker.")
        else:
            self.inbox.put(("resume",))

    def call_on(self, key: str) -> None:
        with self.lock:
            if key in self.hands:
                self.hands.remove(key)
            self.hands.insert(0, key)
            self.you_hand = False
            self.hold = False
        self.add("system", f"You called on {self.name(key)}")
        self.inbox.put(("resume",))

    def lower(self, key: str) -> None:
        with self.lock:
            if key in self.hands:
                self.hands.remove(key)

    def set_muted(self, key: str, on: bool) -> None:
        with self.lock:
            (self.muted.add if on else self.muted.discard)(key)
            if on and key in self.hands:
                self.hands.remove(key)
        self.add("system", f"{self.name(key)} was {'muted' if on else 'unmuted'}")

    def invite(self, key: str) -> None:
        if key in self.agents and key not in self.attendees:
            self.attendees.append(key)
            self.add("system", f"{self.name(key)} joined the meeting")

    def remove(self, key: str) -> None:
        if key in self.attendees:
            self.attendees.remove(key)
            self.lower(key)
            self.add("system", f"{self.name(key)} left the meeting")

    def set_hold(self, on: bool) -> None:
        with self.lock:
            self.hold = on
        self.add("system", "Holding the floor: nobody speaks until you do" if on else "Floor open again")
        if not on:
            self.inbox.put(("resume",))

    def go_around(self) -> None:
        with self.lock:
            order = self._present()
            if "team_lead" in order:  # the lead sums up last
                order.remove("team_lead")
                order.append("team_lead")
            self.hands = order
            self.hand_why = {k: "round the room" for k in order}
            self.you_hand = False
            self.hold = False
        self.add("system", "Going around the room: everyone gives a quick update")
        self.inbox.put(("around",))

    def agenda_action(self, action: str, index: int = -1, text: str = "") -> None:
        with self.lock:
            if action == "add" and text.strip():
                self.agenda.append({"text": text.strip()[:200], "done": False})
                if self.current < 0:
                    self.current = 0
            elif action == "toggle" and 0 <= index < len(self.agenda):
                self.agenda[index]["done"] = not self.agenda[index]["done"]
            elif action == "next" and self.agenda:
                if 0 <= self.current < len(self.agenda):
                    self.agenda[self.current]["done"] = True
                nxt = next((i for i, a in enumerate(self.agenda) if not a["done"]), -1)
                self.current = nxt
                if nxt >= 0:
                    self.add("system", f"Next agenda item: {self.agenda[nxt]['text']}")
                    self.inbox.put(("agenda",))
                else:
                    self.add("system", "That's everything on the agenda")
            elif action == "goto" and 0 <= index < len(self.agenda):
                self.current = index
                self.add("system", f"Moving to: {self.agenda[index]['text']}")
                self.inbox.put(("agenda",))

    def wrap_up(self) -> None:
        if self.status == "live":
            self.status = "wrapping"
            self.add("system", "Wrapping up: writing the notes and action items…")
            self.inbox.put(("wrap",))

    def end(self) -> None:
        if self.status in ("live", "wrapping"):
            self.status = "ended"
            self.ended = time.time()
            self.add("system", "Meeting ended")
            self._save()
            self.inbox.put(("stop",))

    # ---- the engine ------------------------------------------------------------------------------------
    def _loop(self) -> None:
        while True:
            item = self.inbox.get()
            kind = item[0]
            if kind == "stop":
                return
            try:
                if kind == "wrap":
                    self._wrap()
                    return
                if kind == "user":
                    self._moderate(trigger="you", mentions=self._mentions(item[1]))
                elif kind == "agenda":
                    self._moderate(trigger="agenda")
                self._floor()
            except Exception as e:  # keep the meeting alive
                self.add("system", f"Something went wrong: {type(e).__name__}: {str(e)[:160]}")
                self.thinking = None

    def _mentions(self, text: str) -> list[str]:
        low = text.lower()
        out = []
        for k in self._present():
            a = self.agents[k]
            first = a.name.split()[0].lower()
            person = (a.person or "").lower()
            if (f"@{k}" in low or f"@{a.name.lower()}" in low or f"@{first}" in low
                    or re.search(rf"\b{re.escape(a.name.lower())}\b", low) or (person and re.search(rf"\b{re.escape(person)}\b", low))):
                out.append(k)
        if re.search(r"\b(everyone|everybody|all of you|each of you|the team|team,)\b", low):
            out += [k for k in self._present() if k not in out]
        return out

    def _moderate(self, trigger: str, mentions: list[str] | None = None, followup: bool = False) -> None:
        """One cheap call: who wants to speak, and who reacts with which emoji."""
        present = self._present()
        if not present:
            return
        roster = "\n".join(f"- {k}: {self.agents[k].display}, {self.agents[k].role}" for k in present)
        last_speaker = self.entries[-1]["speaker"] if self.entries else "you"
        instructions = (
            "You moderate a live meeting between a business owner and their AI team. Decide who should "
            "speak next and how people react. Reply with JSON only:\n"
            '{"hands": [{"key": "...", "why": "4-8 words"}], "reactions": [{"key": "...", "emoji": "👍"}]}\n'
            "Rules: raise a hand only for people with something genuinely relevant to add for their role "
            "(usually 1-3 people; the person directly asked goes first; for open questions to the team, "
            "include everyone relevant). Order by relevance. "
            + ("This is a FOLLOW-UP check after someone spoke: only raise hands if someone needs to answer, "
               "correct, or build on what was just said; otherwise return no hands. Never the person who just spoke. "
               if followup else "")
            + f"Reactions: 0-2 people may react to the last message with one of {' '.join(EMOJIS)} "
              "when it naturally fits (good news, a joke, a decision). Use only keys from the attendee list.")
        content = (f"Meeting: {self.title}\nAgenda:\n{self._agenda_text()}\n\nAttendees:\n{roster}\n\n"
                   f"Transcript (latest last):\n{self._transcript(20)}\n\nLast speaker: {self.name(last_speaker)}")
        resp = core.client().messages.create(model=MODERATOR_MODEL, max_tokens=400, system=instructions,
                                             messages=[{"role": "user", "content": content}])
        c = core._cost(MODERATOR_MODEL, resp.usage)
        self.cost += c
        core.COST_LOG["Meeting moderator"] += c
        usage.record("Meeting moderator", c)
        plan = _json_from("".join(b.text for b in resp.content if b.type == "text"))
        for h in plan.get("hands", []):
            if h.get("key") in present and h.get("why"):
                self.hand_why[h["key"]] = h["why"][:60]
        for r in plan.get("reactions", [])[:2]:
            if r.get("key") in present and r.get("emoji") in EMOJIS and r.get("key") != last_speaker:
                self.react(r["key"], r["emoji"])
        with self.lock:
            order = [k for k in (mentions or []) if k in present]
            for h in plan.get("hands", []):
                k = h.get("key")
                if k in present and k not in order and not (followup and k == last_speaker):
                    order.append(k)
                    self.hand_why[k] = (h.get("why") or "")[:60]
            for k in order:
                if k not in self.hands:
                    self.hands.append(k)
                    self.add(k, self.hand_why.get(k) or "wants to speak", "hand")

    def _floor(self) -> None:
        turns, followups = 0, 0
        while turns < MAX_TURNS_PER_ROUND and self.status == "live":
            with self.lock:
                if self.you_hand or self.hold or not self.inbox.empty():
                    return
                if not self.hands:
                    key = None
                else:
                    key = self.hands.pop(0)
            if key is None:
                if followups >= 1 or turns == 0:
                    return
                followups += 1
                self._moderate(trigger="followup", followup=True)
                if not self.hands:
                    return
                continue
            if key not in self._present():
                continue
            self._speak(key)
            turns += 1

    def _speak(self, key: str) -> None:
        a = self.agents[key]
        self.thinking = key
        others = ", ".join(self.agents[k].display for k in self.attendees if k != key)
        current = (f"Current agenda item: {self.agenda[self.current]['text']}" if 0 <= self.current < len(self.agenda)
                   else "")
        prompt = (
            f"[LIVE TEAM MEETING: '{self.title}'. You're in the room with {OWNER} (the owner) and {others}.]\n"
            f"Agenda:\n{self._agenda_text()}\n{current}\n\nTranscript so far (latest last):\n{self._transcript(30)}\n\n"
            f"It's your turn to speak, {a.person or a.name}"
            + (f" (you raised your hand: {self.hand_why[key]})" if self.hand_why.get(key) else "") + ".\n"
            "Speak out loud as yourself: natural, first person, warm and to the point. Usually 1-4 short sentences "
            "(about 70 words max) unless the owner asked for detail. This will be read aloud, so no markdown, "
            "bullet points or headings. Address people by name when replying to them. If you need facts "
            "(calendar, tasks, website, the web), check with your tools first. If the owner asked you to do "
            "something now, do it and say what you did. Don't repeat what others already said.")
        run_id = "m" + uuid.uuid4().hex[:9]
        core.begin_run(run_id)
        reply = a.run(prompt, label=f"Speaking in the meeting '{self.title}'")
        self.cost += core.RUNS.get(run_id, {}).get("cost", 0.0)
        self.thinking = None
        with self.lock:
            self.hand_why.pop(key, None)
        self.add(key, _plain(reply) or "(nothing to add)", "speech", run=run_id)

    def _wrap(self) -> None:
        self.thinking = "team_lead" if "team_lead" in self.agents else None
        roster = "\n".join(f"- {k}: {self.agents[k].display}" for k in self.attendees)
        instructions = (
            "Write the minutes of this meeting. Reply with JSON only:\n"
            '{"summary": "3-5 sentences", "decisions": ["..."], '
            '"action_items": [{"task": "...", "owner": "<attendee key or you>", "due": "YYYY-MM-DD or empty"}], '
            '"open_questions": ["..."]}\n'
            "Only include decisions and action items actually agreed in the transcript. Owners must be attendee keys "
            "from the list, or 'you' for the owner.")
        model = self.agents["team_lead"].model if "team_lead" in self.agents else MODERATOR_MODEL
        resp = core.client().messages.create(
            model=model, max_tokens=1500, system=instructions,
            messages=[{"role": "user", "content": f"Meeting: {self.title}\nAgenda:\n{self._agenda_text()}\n"
                                                  f"Attendees:\n{roster}\n\nTranscript:\n{self._transcript(200)}"}])
        c = core._cost(model, resp.usage)
        self.cost += c
        usage.record("Meeting moderator", c)
        m = _json_from("".join(b.text for b in resp.content if b.type == "text")) or {"summary": "No notes could be written."}
        created = 0
        for item in m.get("action_items", [])[:20]:
            owner = item.get("owner") if item.get("owner") in self.agents else "you"
            if item.get("task"):
                add_task(item["task"][:200], owner, details=f"From meeting '{self.title}'", project="",
                         due=item.get("due") or "")
                created += 1
        self.minutes = m
        self.thinking = None
        self.status = "ended"
        self.ended = time.time()
        self.add("system", f"Meeting ended. Notes saved and {created} action item{'s' if created != 1 else ''} added to the task board.")
        self._save()

    # ---- saving + state ------------------------------------------------------------------------
    def _save(self) -> None:
        MEETINGS_DIR.mkdir(parents=True, exist_ok=True)
        data = {"id": self.id, "title": self.title, "started": self.started, "ended": self.ended,
                "attendees": [{"key": k, "name": self.full(k), "icon": self.agents[k].icon} for k in self.attendees],
                "agenda": self.agenda, "minutes": self.minutes, "cost": round(self.cost, 5),
                "transcript": [e for e in self.entries if e["kind"] in ("speech", "system")]}
        (MEETINGS_DIR / f"{self.id}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    def state(self, since: int = 0) -> dict:
        with self.lock:
            doing = core.STATUS.get(self.thinking, {}).get("doing", "") if self.thinking else ""
            return {
                "id": self.id, "status": self.status, "title": self.title, "started": self.started,
                "ended": self.ended, "cost": round(self.cost, 5), "attendees": list(self.attendees),
                "muted": sorted(self.muted), "hands": list(self.hands), "hand_why": dict(self.hand_why),
                "you_hand": self.you_hand, "hold": self.hold, "thinking": self.thinking, "doing": doing,
                "agenda": self.agenda, "current": self.current, "minutes": self.minutes,
                "entries": [e for e in self.entries if e["id"] > since], "last_id": self._eid,
            }


def history() -> list[dict]:
    if not MEETINGS_DIR.exists():
        return []
    out = []
    for f in sorted(MEETINGS_DIR.glob("*.json"), reverse=True)[:30]:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            out.append({"id": d["id"], "title": d["title"], "started": d["started"], "ended": d.get("ended"),
                        "attendees": d.get("attendees", []), "has_minutes": bool(d.get("minutes")),
                        "cost": d.get("cost", 0)})
        except (json.JSONDecodeError, KeyError):
            continue
    return out


def load_meeting(mid: str) -> dict | None:
    f = MEETINGS_DIR / f"{re.sub(r'[^0-9A-Za-z-]', '', mid)}.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
