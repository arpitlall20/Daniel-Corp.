"""Shared task board + project list, stored in a local SQLite file (no extra app needed)."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .base import I, S, Tool, schema

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "team.db"


def _db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY, name TEXT UNIQUE, description TEXT,
            deadline TEXT, status TEXT DEFAULT 'active', created TEXT);
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY, title TEXT, details TEXT, owner TEXT,
            project TEXT, status TEXT DEFAULT 'todo', due TEXT,
            result TEXT, created TEXT, updated TEXT);
        """
    )
    return con


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def add_task(title: str, owner: str, details: str = "", project: str = "", due: str = "") -> str:
    with _db() as con:
        cur = con.execute(
            "INSERT INTO tasks (title, details, owner, project, due, created, updated) VALUES (?,?,?,?,?,?,?)",
            (title, details, owner, project, due, _now(), _now()),
        )
    return f"Task #{cur.lastrowid} created for {owner}."


def update_task(task_id: int, status: str = "", result: str = "", owner: str = "") -> str:
    fields, values = [], []
    for col, val in (("status", status), ("result", result), ("owner", owner)):
        if val:
            fields.append(f"{col}=?")
            values.append(val)
    if not fields:
        return "Nothing to update."
    fields.append("updated=?")
    values += [_now(), task_id]
    with _db() as con:
        con.execute(f"UPDATE tasks SET {', '.join(fields)} WHERE id=?", values)
    return f"Task #{task_id} updated."


def list_tasks(owner: str = "", status: str = "", project: str = "") -> str:
    q, args = "SELECT * FROM tasks WHERE 1=1", []
    for col, val in (("owner", owner), ("status", status), ("project", project)):
        if val:
            q += f" AND {col}=?"
            args.append(val)
    with _db() as con:
        rows = [dict(r) for r in con.execute(q + " ORDER BY id DESC LIMIT 50", args)]
    return json.dumps(rows, indent=1) if rows else "No tasks found."


def add_project(name: str, description: str = "", deadline: str = "") -> str:
    with _db() as con:
        con.execute(
            "INSERT OR REPLACE INTO projects (name, description, deadline, created) VALUES (?,?,?,?)",
            (name, description, deadline, _now()),
        )
    return f"Project '{name}' saved."


def list_projects() -> str:
    with _db() as con:
        rows = [dict(r) for r in con.execute("SELECT * FROM projects ORDER BY deadline")]
    return json.dumps(rows, indent=1) if rows else "No projects yet."


OWNERS = {"type": "string", "description": "a team member key (e.g. team_lead, assistant, travel, web, senior_dev, junior_dev), or 'you' for the owner"}

TASK_TOOLS = [
    Tool("add_task", "Add a task to the shared task board.",
         schema({"title": S, "owner": OWNERS, "details": S, "project": S,
                 "due": {"type": "string", "description": "ISO date, optional"}}, ["title", "owner"]), add_task),
    Tool("update_task", "Update a task's status (todo, doing, review, done, blocked), result or owner.",
         schema({"task_id": I, "status": S, "result": S, "owner": OWNERS}, ["task_id"]), update_task),
    Tool("list_tasks", "List tasks, optionally filtered by owner, status or project.",
         schema({"owner": S, "status": S, "project": S}), list_tasks),
]

PROJECT_TOOLS = [
    Tool("add_project", "Create or update a project with an optional deadline (ISO date).",
         schema({"name": S, "description": S, "deadline": S}, ["name"]), add_project),
    Tool("list_projects", "List all projects and deadlines.", schema({}), list_projects),
]
