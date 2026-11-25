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
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from dotenv import load_dotenv
    load_dotenv(override=True)  # .env always wins over old Windows settings
except ImportError:
    pass

from team import approvals, chats, usage  # noqa: E402
from team import agent as core  # noqa: E402
from team.config import load, set_enabled, set_model  # noqa: E402
from team import org  # noqa: E402
from team.tools.memory import MEMORY, memory_text  # noqa: E402
from team.tools.tasks import list_projects, list_tasks  # noqa: E402

HOST, PORT = "127.0.0.1", int(os.getenv("PORT", "8000"))
PAGE = Path(__file__).parent / "web" / "index.html"

AGENTS = org.build_all()   # live dict: agents you add or move update it in place
TRANSCRIPTS: dict[str, list] = {k: chats.load(k) for k in AGENTS}   # saved on disk, survives restarts


def transcript(key: str) -> list:
    if key not in TRANSCRIPTS:
        TRANSCRIPTS[key] = chats.load(key)
    return TRANSCRIPTS[key]
ACTIVE: dict[str, dict] = {}   # run id -> {member, started, text}

# What each agent can't do (shown on its profile, so nobody has to guess)
LIMITS = {
    "team_lead": ["Doesn't do hands-on work itself; it delegates and checks", "Only sees what the team has done"],
    "assistant": ["Asks you before creating or deleting events", "Doesn't email or invite other people"],
    "travel": ["Never books or pays: it gives options and booking links", "Prices come from live web searches and can change"],
    "web": ["Everything starts as a draft", "Publishing or editing live pages needs your approval", "Has no delete button"],
    "senior_dev": ["Never commits to main; works on branches and pull requests", "Merging needs your approval"],
    "junior_dev": ["Small, well-defined tasks only", "Can't merge; the senior reviews everything"],
}

# ---- approvals in the browser -------------------------------------------------
PENDING: dict[int, dict] = {}
_ids = itertools.count(1)
_plock = threading.Lock()
APPROVAL_TIMEOUT = 600


def web_approval(action: str) -> bool:
    key = core.current_agent()
    item = {"id": next(_ids), "action": action, "event": threading.Event(), "approved": False,
            "agent": key, "run": core.current_run(), "created": time.time()}
    with _plock:
        PENDING[item["id"]] = item
    if key:
        core.set_status(key, "waiting", "Waiting for your approval")
    core.log_event(AGENTS[key].name if key else "Team", f"needs your approval: {action[:160]}", "approval",
                   key=key, detail=action)
    answered = item["event"].wait(timeout=APPROVAL_TIMEOUT)
    with _plock:
        PENDING.pop(item["id"], None)
    if not answered:
        core.log_event("You", "approval timed out (treated as No)", "declined", key=key)
    if key:
        core.set_status(key, "working", "Thinking…")
    return item["approved"]


approvals.set_handler(web_approval)


# ---- background runs ----------------------------------------------------------
def start_run(key: str, text: str) -> str:
    run_id = uuid.uuid4().hex[:10]
    ACTIVE[run_id] = {"run": run_id, "member": key, "started": time.time(), "text": text}
    threading.Thread(target=_run, args=(run_id, key, text), daemon=True).start()
    return run_id


def _run(run_id: str, key: str, text: str) -> None:
    core.begin_run(run_id)
    history = chats.model_history(transcript(key))
    transcript(key).append({"role": "you", "text": text, "ts": time.time(), "run": run_id})
    chats.save(key, transcript(key))
    core.log_event("You", f"to {AGENTS[key].name}: {text[:160]}", "you", key="you", target=key, detail=text)
    status = "done"
    try:
        reply = AGENTS[key].run(text, history) or "(no reply)"
    except core.StopRequested:
        reply, status = "⏹️ Stopped. You pressed Stop, so this wasn't finished.", "stopped"
    except Exception as e:  # show errors in the chat instead of crashing
        traceback.print_exc()
        reply, status = f"⚠️ Error: {type(e).__name__}: {e}", "error"
    for k, st in list(core.STATUS.items()):   # tidy up any agent left mid-way by a stop or crash
        if st.get("run") == run_id and st.get("state") in ("working", "delegating", "waiting"):
            core.set_status(k, "idle", "", task="")
    info = core.RUNS.get(run_id, {})
    transcript(key).append({"role": "agent", "text": reply, "ts": time.time(), "run": run_id, "status": status,
                             "secs": round(time.time() - ACTIVE[run_id]["started"], 1),
                             "cost": round(info.get("cost", 0.0), 5), "steps": info.get("steps", 0)})
    chats.save(key, transcript(key))
    core.log_event(AGENTS[key].name if key in AGENTS else key, f"request {status}", "run_end", key=key)
    ACTIVE.pop(run_id, None)
    core.CANCELLED.discard(run_id)


def stop_run(run_id: str) -> None:
    core.CANCELLED.add(run_id)
    with _plock:                      # an agent waiting on you is released straight away
        for item in PENDING.values():
            if item["run"] == run_id:
                item["approved"] = False
                item["event"].set()
    core.log_event("You", "pressed Stop", "stopped", key="you")


# ---- state for the page ---------------------------------------------------------
def team_state() -> dict:
    cfg = load()["agents"]
    spent = usage.summary()["by_agent"]
    busy = {r["member"] for r in ACTIVE.values()}
    reports = org.parents()
    members = []
    for k in org.ordered_keys():
        a = AGENTS[k]
        enabled = cfg.get(k, {}).get("enabled", False)
        st = core.STATUS.get(k, {"state": "idle", "doing": "", "task": "", "since": 0})
        state = st["state"] if (enabled or st["state"] != "idle") else "off"
        members.append({"key": k, "name": a.name, "model": a.model, "enabled": enabled, "state": state,
                        "doing": st.get("doing", ""), "task": st.get("task", ""), "since": st.get("since", 0),
                        "busy": k in busy, "reports_to": reports.get(k, "team_lead"), "manages": list(a.team),
                        "icon": a.icon, "role": a.role, "custom": a.custom,
                        "cost": round(core.COST_LOG.get(a.name, 0.0), 5), "cost_all": spent.get(a.name, 0.0)})
    return {"members": members}


def approvals_state() -> list:
    with _plock:
        return [{"id": i["id"], "action": i["action"], "agent": i["agent"], "run": i["run"],
                 "expires": i["created"] + APPROVAL_TIMEOUT} for i in PENDING.values()]


def active_state() -> list:
    return [{**r, "cost": round(core.RUNS.get(r["run"], {}).get("cost", 0.0), 5),
             "steps": core.RUNS.get(r["run"], {}).get("steps", 0)} for r in ACTIVE.values()]


def _safe_load(line: str):
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return None


def run_events(run_id: str) -> list:
    evs = [e for e in core.EVENTS if e.get("run") == run_id]
    if evs:
        return evs
    try:  # older runs: read from the saved activity log
        with core.ACTIVITY_LOG.open(encoding="utf-8") as f:
            return [e for e in map(_safe_load, f) if e and e.get("run") == run_id]
    except FileNotFoundError:
        return []


def _team_of(key: str) -> set:
    p, found, frontier = org.parents(), set(), {key}
    while frontier:
        frontier = {k for k, b in p.items() if b in frontier and k not in found}
        found |= frontier
    return found


def profile(key: str) -> dict:
    a = AGENTS[key]
    recent = [e for e in core.EVENTS if e.get("key") == key and e.get("kind") not in ("start", "done")][-12:]
    return {"key": key, "name": a.name, "model": a.model, "models": list(core.PRICES),
            "prices": {m: p[:2] for m, p in core.PRICES.items()},
            "reports_to": org.parents().get(key, "team_lead"), "manages": [AGENTS[k].name for k in a.team],
            "icon": a.icon, "role": a.role, "custom": a.custom, "spec": org.spec_of(key),
            "can_report_to": ["you"] + [k for k in org.ordered_keys() if k != key and k not in _team_of(key)],
            "tools": [{"name": t.name, "description": t.description.split(". ")[0]} for t in a.tools]
                     + [{"name": s.get("name", "tool"), "description": "Search the web for current information"}
                        for s in a.server_tools],
            "limits": LIMITS.get(key, ["Only has the abilities you gave it", "Asks you before anything risky"]), "cost_session": round(core.COST_LOG.get(a.name, 0.0), 5),
            "cost_all": usage.summary()["by_agent"].get(a.name, 0.0), "recent": recent[::-1]}


# ---- connections ------------------------------------------------------------------
CONNECTIONS = {
    "claude": ("Claude API", ["ANTHROPIC_API_KEY"], "Every agent"),
    "calendar": ("Apple Calendar", ["ICLOUD_EMAIL", "ICLOUD_APP_PASSWORD"], "Calendar assistant, travel agent"),
    "wordpress": ("WordPress", ["WP_URL", "WP_USERNAME", "WP_APP_PASSWORD"], "Web & content agent"),
    "github": ("GitHub", ["GITHUB_TOKEN", "GITHUB_REPO", "REPO_PATH"], "Developers"),
}


def health() -> list:
    return [{"id": cid, "name": n, "used_by": u, "configured": all(os.getenv(v, "").strip() for v in vars_),
             "missing": [v for v in vars_ if not os.getenv(v, "").strip()]}
            for cid, (n, vars_, u) in CONNECTIONS.items()]


def test_connection(cid: str) -> dict:
    try:
        if cid == "claude":
            core.client().models.list(limit=1)
            msg = "Connected. Your API key works."
        elif cid == "calendar":
            from team.tools import calendar as cal
            cal._calendar = None
            msg = f"Connected to iCloud. Using calendar: {cal._cal().name}"
        elif cid == "wordpress":
            from team.tools.wordpress import wp_site_info
            msg = wp_site_info().replace("\n", " · ")
        elif cid == "github":
            from team.tools.code import _gh_repo
            msg = f"Connected to {_gh_repo().full_name}."
        else:
            return {"ok": False, "message": "Unknown connection"}
        return {"ok": True, "message": msg}
    except Exception as e:
        return {"ok": False, "message": f"{type(e).__name__}: {str(e)[:220]}"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the terminal quiet
        pass

    def _send(self, code: int, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode()
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
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        path = u.path
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/poll":
            since = int(q.get("since", 0) or 0)
            evs = [e for e in core.EVENTS if e["id"] > since][-300:]
            return self._send(200, {"events": evs, **team_state(), "approvals": approvals_state(),
                                    "active": active_state(), "usage": usage.summary(), "now": time.time()})
        if path == "/api/team":
            return self._send(200, {**team_state(), "usage": usage.summary()})
        if path == "/api/transcript":
            key = q.get("member", "")
            return self._send(200, transcript(key) if key in AGENTS else [])
        if path == "/api/abilities":
            return self._send(200, {"abilities": org.abilities_catalog(), "models": org.MODELS})
        if path == "/api/run":
            return self._send(200, run_events(q.get("id", "")))
        if path == "/api/agent":
            key = q.get("key", "")
            return self._send(200, profile(key)) if key in AGENTS else self._send(404, {"error": "unknown agent"})
        if path == "/api/activity":
            since = int(q.get("since", 0) or 0)
            return self._send(200, [e for e in core.EVENTS if e["id"] > since])
        if path == "/api/approvals":
            return self._send(200, approvals_state())
        if path == "/api/memory":
            return self._send(200, {"text": memory_text()})
        if path == "/api/usage":
            return self._send(200, {**usage.summary(), "session": round(sum(core.COST_LOG.values()), 4)})
        if path == "/api/health":
            return self._send(200, health())
        if path == "/api/tasks":
            return self._send(200, {"tasks": json.loads(_safe_json(list_tasks())),
                                    "projects": json.loads(_safe_json(list_projects()))})
        self._send(404, {"error": "not found"})

    def do_POST(self):
        path, body = urlparse(self.path).path, self._json()
        if path == "/api/toggle":
            msg = set_enabled(body["key"], bool(body["enabled"]))
            core.log_event("You", msg, "system", key="you", target=body["key"])
            return self._send(200, {"message": msg, **team_state()})
        if path == "/api/model":
            key, model = body.get("key"), body.get("model")
            if key not in AGENTS or model not in core.PRICES:
                return self._send(400, {"error": "Unknown agent or model"})
            AGENTS[key].model = model
            set_model(key, model)
            core.log_event("You", f"switched {AGENTS[key].name} to {model}", "system", key="you", target=key)
            return self._send(200, {"ok": True})
        if path == "/api/approve":
            with _plock:
                item = PENDING.get(int(body["id"]))
            if not item:
                return self._send(404, {"error": "already answered"})
            item["approved"] = bool(body["approved"])
            item["event"].set()
            core.log_event("You", ("approved: " if item["approved"] else "declined: ") + item["action"][:120],
                           "approved" if item["approved"] else "declined", key="you", target=item["agent"])
            return self._send(200, {"ok": True})
        if path == "/api/stop":
            run_id = body.get("run", "")
            if run_id not in ACTIVE:
                return self._send(404, {"error": "That request already finished."})
            stop_run(run_id)
            return self._send(200, {"ok": True})
        if path == "/api/reset":
            key = body["member"]
            transcript(key).clear(); chats.clear(key)
            return self._send(200, {"ok": True})
        if path == "/api/memory":
            MEMORY.parent.mkdir(exist_ok=True)
            MEMORY.write_text((body.get("text") or "").strip() + "\n", encoding="utf-8")
            core.log_event("You", "edited team memory", "system", key="you")
            return self._send(200, {"ok": True})
        if path == "/api/org":
            try:
                msg = org.set_parent(body.get("key", ""), body.get("reports_to", ""))
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            core.log_event("You", msg, "system", key="you", target=body.get("key"))
            return self._send(200, {"message": msg, **team_state()})
        if path == "/api/agents/add":
            try:
                key = org.add_agent(body)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            core.log_event("You", f"added {AGENTS[key].name} to the team", "system", key="you", target=key)
            return self._send(200, {"key": key, "message": f"Welcome, {AGENTS[key].name}!", **team_state()})
        if path == "/api/agents/update":
            try:
                org.update_agent(body.get("key", ""), body)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            return self._send(200, {"message": "Saved", **team_state()})
        if path == "/api/agents/remove":
            key = body.get("key", "")
            if any(r["member"] == key for r in ACTIVE.values()):
                return self._send(409, {"error": "They're busy right now. Stop the request first."})
            try:
                msg = org.remove_agent(key)
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            core.log_event("You", msg, "system", key="you")
            return self._send(200, {"message": msg, **team_state()})
        if path == "/api/health/test":
            return self._send(200, test_connection(body.get("id", "")))
        if path == "/api/chat":
            key, text = body.get("member"), (body.get("message") or "").strip()
            if key not in AGENTS or not text:
                return self._send(400, {"error": "Pick a team member and type a message."})
            if not load()["agents"].get(key, {}).get("enabled"):
                return self._send(409, {"error": f"{AGENTS[key].name} is switched off."})
            if any(r["member"] == key for r in ACTIVE.values()):
                return self._send(409, {"error": f"{AGENTS[key].name} is still working on your last message."})
            return self._send(200, {"run": start_run(key, text)})
        self._send(404, {"error": "not found"})


def _safe_json(text: str) -> str:
    return text if text.strip().startswith("[") else "[]"


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    url = f"http://localhost:{PORT}"
    print(f"Your AI team control panel is running at {url}  (Ctrl+C to stop)")
    if "--no-browser" not in sys.argv:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        print(f"\nStopped. Session cost estimate: ${sum(core.COST_LOG.values()):.4f} USD")


if __name__ == "__main__":
    main()
