"""Web control panel for your AI team.

    python web.py            # opens http://localhost:8000 in your browser

Runs only on your own computer (127.0.0.1); nothing is exposed to the internet.
Uses only Python's standard library, so there is nothing extra to install.
"""
from __future__ import annotations

import itertools
import json
import os
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)  # .env always wins over old Windows settings
except ImportError:
    pass

from team import approvals, chats, usage  # noqa: E402
from team.agent import COST_LOG, EVENTS, log_event  # noqa: E402
from team.config import load, set_enabled  # noqa: E402
from team.roster import all_agents, build_team  # noqa: E402
from team.tools.memory import MEMORY, memory_text  # noqa: E402
from team.tools.tasks import list_projects, list_tasks  # noqa: E402

HOST, PORT = "127.0.0.1", int(os.getenv("PORT", "8000"))
PAGE = Path(__file__).parent / "web" / "index.html"

AGENTS = all_agents(build_team())
ORDER = ["team_lead", "assistant", "travel", "senior_dev", "junior_dev"]
ORDER += [k for k in AGENTS if k not in ORDER]   # agents you add later show up automatically
REPORTS_TO = {}
for k, a in AGENTS.items():
    for sub in a.team:
        REPORTS_TO[sub] = k
TRANSCRIPTS: dict[str, list] = {k: chats.load(k) for k in AGENTS}   # saved on disk, survives restarts
LOCKS = {k: threading.Lock() for k in AGENTS}

# ---- approvals in the browser -------------------------------------------------
PENDING: dict[int, dict] = {}
_ids = itertools.count(1)
_plock = threading.Lock()


def web_approval(action: str) -> bool:
    item = {"id": next(_ids), "action": action, "event": threading.Event(), "approved": False}
    with _plock:
        PENDING[item["id"]] = item
    log_event("You", f"approval requested: {action[:120]}")
    answered = item["event"].wait(timeout=600)       # 10 minutes to decide
    with _plock:
        PENDING.pop(item["id"], None)
    if not answered:
        log_event("You", "approval timed out (treated as No)")
    return item["approved"]


approvals.set_handler(web_approval)


def team_state() -> dict:
    cfg = load()["agents"]
    members = []
    for k in ORDER:
        a = AGENTS[k]
        members.append({
            "key": k, "name": a.name, "model": a.model,
            "enabled": cfg.get(k, {}).get("enabled", False),
            "busy": LOCKS[k].locked(), "reports_to": REPORTS_TO.get(k, "you"),
            "cost": round(COST_LOG.get(a.name, 0.0), 4),
        })
    return {"members": members, "total_cost": round(sum(COST_LOG.values()), 4), "usage": usage.summary()}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the terminal quiet
        pass

    def _send(self, code: int, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self) -> dict:
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"{}")

    def do_GET(self):
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/team":
            return self._send(200, team_state())
        if path == "/api/transcript":
            key = self.path.split("member=")[-1]
            return self._send(200, TRANSCRIPTS.get(key, []))
        if path == "/api/activity":
            since = int(self.path.split("since=")[-1]) if "since=" in self.path else 0
            return self._send(200, [e for e in EVENTS if e["id"] > since])
        if path == "/api/memory":
            return self._send(200, {"text": memory_text()})
        if path == "/api/usage":
            return self._send(200, {**usage.summary(), "session": round(sum(COST_LOG.values()), 4)})
        if path == "/api/approvals":
            with _plock:
                return self._send(200, [{"id": i["id"], "action": i["action"]} for i in PENDING.values()])
        if path == "/api/tasks":
            return self._send(200, {"tasks": json.loads(_safe_json(list_tasks())),
                                    "projects": json.loads(_safe_json(list_projects()))})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path, body = self.path.split("?")[0], self._json()
        if path == "/api/toggle":
            msg = set_enabled(body["key"], bool(body["enabled"]))
            log_event("You", msg)
            return self._send(200, {"message": msg, **team_state()})
        if path == "/api/approve":
            with _plock:
                item = PENDING.get(int(body["id"]))
            if not item:
                return self._send(404, {"error": "already answered"})
            item["approved"] = bool(body["approved"])
            item["event"].set()
            log_event("You", ("approved: " if item["approved"] else "declined: ") + item["action"][:100])
            return self._send(200, {"ok": True})
        if path == "/api/reset":
            key = body["member"]
            TRANSCRIPTS[key].clear(); chats.clear(key)
            return self._send(200, {"ok": True})
        if path == "/api/memory":
            MEMORY.parent.mkdir(exist_ok=True)
            MEMORY.write_text((body.get("text") or "").strip() + "\n", encoding="utf-8")
            log_event("You", "edited team memory")
            return self._send(200, {"ok": True})
        if path == "/api/chat":
            key, text = body.get("member"), (body.get("message") or "").strip()
            if key not in AGENTS or not text:
                return self._send(400, {"error": "Pick a team member and type a message."})
            lock = LOCKS[key]
            if not lock.acquire(blocking=False):
                return self._send(409, {"error": f"{AGENTS[key].name} is still working on your last message."})
            try:
                history = chats.model_history(TRANSCRIPTS[key])
                TRANSCRIPTS[key].append({"role": "you", "text": text, "ts": time.time()})
                chats.save(key, TRANSCRIPTS[key])
                log_event("You", f"to {AGENTS[key].name}: {text[:120]}")
                try:
                    reply = AGENTS[key].run(text, history) or "(no reply)"
                except Exception as e:  # show errors in the chat instead of crashing
                    traceback.print_exc()
                    reply = f"⚠️ Error: {type(e).__name__}: {e}"
                TRANSCRIPTS[key].append({"role": "agent", "text": reply, "ts": time.time()})
                chats.save(key, TRANSCRIPTS[key])
                return self._send(200, {"reply": reply, **team_state()})
            finally:
                lock.release()
        self._send(404, {"error": "not found"})


def _safe_json(text: str) -> str:
    return text if text.strip().startswith("[") else "[]"


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Your AI team control panel is running at {url}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        total = sum(COST_LOG.values())
        print(f"\nStopped. Session cost estimate: ${total:.4f} USD")


if __name__ == "__main__":
    main()
