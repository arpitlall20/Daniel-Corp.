"""Code tools for the developers: files, commands, git and GitHub pull requests.

All work happens in REPO_PATH (a local clone of your GitHub repo). Nobody pushes to main:
work goes on branches -> pull requests -> senior review -> you approve the merge.
"""
from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

from ..approvals import ask_approval
from .base import I, S, Tool, schema

SAFE_PREFIXES = ("pytest", "python -m pytest", "python -m unittest", "ruff", "git status", "git diff",
                 "git log", "ls", "cat", "grep", "pip list")


def _repo() -> Path:
    p = Path(os.getenv("REPO_PATH", "./workspace")).expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_path(rel: str) -> Path:
    p = (_repo() / rel).resolve()
    if _repo() not in p.parents and p != _repo():
        raise ValueError("Path is outside the repository.")
    return p


def _sh(cmd: str, timeout: int = 300) -> str:
    r = subprocess.run(cmd, shell=True, cwd=_repo(), capture_output=True, text=True, timeout=timeout)
    out = (r.stdout + r.stderr).strip()
    return f"exit={r.returncode}\n{out[-6000:]}"


def list_files(path: str = ".") -> str:
    root = _safe_path(path)
    files = [str(p.relative_to(_repo())) for p in root.rglob("*")
             if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts]
    return "\n".join(sorted(files)[:400]) or "Empty."


def read_file(path: str) -> str:
    return _safe_path(path).read_text()[:20000]


def write_file(path: str, content: str) -> str:
    p = _safe_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return f"Wrote {path} ({len(content)} chars)."


def run_command(command: str) -> str:
    if not command.strip().startswith(SAFE_PREFIXES):
        if not ask_approval(f"Run command in repo: {command}"):
            return "Owner declined the command."
    return _sh(command)


def git_new_branch(name: str) -> str:
    return _sh(f"git checkout -b {shlex.quote(name)}")


def git_commit_and_push(message: str) -> str:
    branch = _sh("git rev-parse --abbrev-ref HEAD").splitlines()[-1].strip()
    if branch in ("main", "master"):
        return "Refused: never commit straight to main. Create a branch first."
    _sh("git add -A")
    commit = _sh(f"git commit -m {shlex.quote(message)}")
    push = _sh(f"git push -u origin {shlex.quote(branch)}")
    return f"{commit}\n{push}"


def _gh_repo():
    from github import Auth, Github  # lazy import

    return Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"])).get_repo(os.environ["GITHUB_REPO"])


def open_pull_request(title: str, body: str, base: str = "main") -> str:
    branch = _sh("git rev-parse --abbrev-ref HEAD").splitlines()[-1].strip()
    pr = _gh_repo().create_pull(title=title, body=body, head=branch, base=base)
    return f"Opened PR #{pr.number}: {pr.html_url}"


def get_pull_request_diff(number: int) -> str:
    pr = _gh_repo().get_pull(number)
    parts = [f"PR #{number}: {pr.title}\n{pr.body or ''}"]
    for f in pr.get_files():
        parts.append(f"--- {f.filename} (+{f.additions}/-{f.deletions})\n{(f.patch or '')[:4000]}")
    return "\n\n".join(parts)[:30000]


def comment_on_pull_request(number: int, comment: str) -> str:
    _gh_repo().get_pull(number).create_issue_comment(comment)
    return "Comment posted."


def merge_pull_request(number: int) -> str:
    pr = _gh_repo().get_pull(number)
    if not ask_approval(f"GitHub: MERGE PR #{number} '{pr.title}' into {pr.base.ref}"):
        return "Owner declined the merge."
    result = pr.merge(merge_method="squash")
    return f"Merged: {result.merged}"


DEV_TOOLS = [
    Tool("list_files", "List files in the repo (or a sub-folder).", schema({"path": S}), list_files),
    Tool("read_file", "Read a file from the repo.", schema({"path": S}, ["path"]), read_file),
    Tool("write_file", "Create or overwrite a file in the repo.", schema({"path": S, "content": S}, ["path", "content"]), write_file),
    Tool("run_command", "Run a shell command in the repo (tests, linters). Unusual commands need owner approval.",
         schema({"command": S}, ["command"]), run_command),
    Tool("git_new_branch", "Create and switch to a new git branch.", schema({"name": S}, ["name"]), git_new_branch),
    Tool("git_commit_and_push", "Commit all changes on the current branch and push it.", schema({"message": S}, ["message"]), git_commit_and_push),
    Tool("open_pull_request", "Open a GitHub pull request from the current branch.",
         schema({"title": S, "body": S, "base": S}, ["title", "body"]), open_pull_request),
]

REVIEW_TOOLS = [
    Tool("get_pull_request_diff", "Read a pull request's description and diff.", schema({"number": I}, ["number"]), get_pull_request_diff),
    Tool("comment_on_pull_request", "Post a review comment on a pull request.",
         schema({"number": I, "comment": S}, ["number", "comment"]), comment_on_pull_request),
    Tool("merge_pull_request", "Merge a pull request (owner must approve).", schema({"number": I}, ["number"]), merge_pull_request),
]
