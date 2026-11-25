"""WordPress tools, using WordPress's official REST API (no clicking around the admin).

Setup: in WordPress go to Users > Profile > Application Passwords, add one called "Daniel Corp",
then put WP_URL, WP_USERNAME and WP_APP_PASSWORD in .env. Test with:  python check_wordpress.py

Safety: new content is always a draft. Publishing, or changing anything that's already live,
needs your approval. There is no delete tool.
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from ..approvals import ask_approval
from .base import I, S, Tool, schema

KINDS = {"post": "posts", "page": "pages"}


def _base() -> str:
    url = os.getenv("WP_URL", "").strip().rstrip("/")
    if not url:
        raise RuntimeError("WP_URL is not set in .env")
    return url


def _api(method: str, path: str, data: dict | None = None, params: dict | None = None):
    url = f"{_base()}/wp-json/wp/v2/{path}"
    if params:
        url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v not in (None, "")})
    token = base64.b64encode(
        f"{os.getenv('WP_USERNAME', '').strip()}:{os.getenv('WP_APP_PASSWORD', '').strip()}".encode()).decode()
    req = urllib.request.Request(url, method=method, headers={
        "Authorization": f"Basic {token}", "Content-Type": "application/json", "User-Agent": "DanielCorp-AI-Team"})
    if data is not None:
        req.data = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"WordPress said {e.code}: {body}") from None


def _kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError("kind must be 'post' or 'page'")
    return KINDS[kind]


def _text(html: str, limit: int) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html or "")).strip()[:limit]


def _links(kind: str, item: dict) -> str:
    edit = f"{_base()}/wp-admin/post.php?post={item['id']}&action=edit"
    view = item.get("link", "")
    if item.get("status") != "publish":
        view = f"{_base()}/?{'page_id' if kind == 'page' else 'p'}={item['id']}&preview=true"
    return f"view: {view} | edit: {edit}"


def _term_ids(taxonomy: str, names: list[str]) -> list[int]:
    ids = []
    for name in names or []:
        found = _api("GET", taxonomy, params={"search": name, "per_page": 20}) or []
        match = next((t for t in found if t["name"].lower() == name.lower()), None)
        if not match:
            match = _api("POST", taxonomy, {"name": name})
        ids.append(match["id"])
    return ids


# ---- tools ------------------------------------------------------------------
def wp_site_info() -> str:
    me = _api("GET", "users/me", params={"context": "edit"})
    root = json.loads(urllib.request.urlopen(f"{_base()}/wp-json", timeout=30).read())
    return f"Site: {root.get('name')} ({_base()}) - {root.get('description', '')}\nConnected as: {me.get('name')} (roles: {', '.join(me.get('roles', []))})"


def wp_list(kind: str = "post", search: str = "", status: str = "any", count: int = 10) -> str:
    items = _api("GET", _kind(kind), params={"search": search, "status": status, "per_page": min(count, 50),
                                             "context": "edit", "orderby": "modified"}) or []
    lines = [f"#{i['id']} [{i['status']}] {_text(i['title'].get('raw') or i['title'].get('rendered', ''), 100)}"
             f" (modified {i.get('modified', '')[:10]})" for i in items]
    return "\n".join(lines) or f"No {kind}s found."


def wp_get(kind: str, id: int) -> str:
    i = _api("GET", f"{_kind(kind)}/{id}", params={"context": "edit"})
    content = i["content"].get("raw") or i["content"].get("rendered", "")
    return (f"#{i['id']} [{i['status']}] {i['title'].get('raw', '')}\nslug: {i.get('slug')}\n{_links(kind, i)}\n"
            f"excerpt: {_text(i.get('excerpt', {}).get('raw', ''), 300)}\n\nCONTENT (HTML):\n{content[:15000]}")


def wp_create_draft(kind: str, title: str, content: str, excerpt: str = "", slug: str = "",
                    categories: list[str] | None = None, tags: list[str] | None = None) -> str:
    data = {"title": title, "content": content, "status": "draft", "excerpt": excerpt, "slug": slug}
    if kind == "post":
        if categories:
            data["categories"] = _term_ids("categories", categories)
        if tags:
            data["tags"] = _term_ids("tags", tags)
    i = _api("POST", _kind(kind), {k: v for k, v in data.items() if v not in ("", None)})
    return f"Draft {kind} #{i['id']} created: '{title}'. Nothing is live yet. {_links(kind, i)}"


def wp_update(kind: str, id: int, title: str = "", content: str = "", excerpt: str = "", slug: str = "") -> str:
    current = _api("GET", f"{_kind(kind)}/{id}", params={"context": "edit"})
    changes = {k: v for k, v in {"title": title, "content": content, "excerpt": excerpt, "slug": slug}.items() if v}
    if not changes:
        return "Nothing to change."
    if current["status"] == "publish":
        summary = ", ".join(changes)
        if not ask_approval(f"WordPress: change LIVE {kind} #{id} '{current['title'].get('raw', '')}' ({summary})\n"
                            f"{current.get('link', '')}"):
            return "Owner declined. The live page was not changed. Tip: offer to make the changes as a draft copy instead."
    i = _api("POST", f"{_kind(kind)}/{id}", changes)
    return f"Updated {kind} #{id} ({', '.join(changes)}). Status: {i['status']}. {_links(kind, i)}"


def wp_publish(kind: str, id: int, schedule_for: str = "") -> str:
    current = _api("GET", f"{_kind(kind)}/{id}", params={"context": "edit"})
    title = current["title"].get("raw", "")
    when = f"at {schedule_for}" if schedule_for else "NOW"
    if not ask_approval(f"WordPress: publish {kind} #{id} '{title}' {when}\nPreview: {_links(kind, current)}"):
        return "Owner declined. It stays a draft."
    data = {"status": "future", "date": schedule_for} if schedule_for else {"status": "publish"}
    i = _api("POST", f"{_kind(kind)}/{id}", data)
    return f"{kind.title()} #{id} is now '{i['status']}'. {i.get('link', '')}"


def wp_list_categories() -> str:
    cats = _api("GET", "categories", params={"per_page": 100}) or []
    return ", ".join(f"{c['name']} ({c['count']})" for c in cats) or "No categories."


KIND = {"type": "string", "enum": ["post", "page"]}

WORDPRESS_TOOLS = [
    Tool("wp_site_info", "Check the WordPress connection: site name and which user you're connected as.", schema({}), wp_site_info),
    Tool("wp_list", "List posts or pages (newest-modified first). status: any, publish, draft, future, pending.",
         schema({"kind": KIND, "search": S, "status": S, "count": I}), wp_list),
    Tool("wp_get", "Read one post or page, including its full HTML content and preview/edit links.",
         schema({"kind": KIND, "id": I}, ["kind", "id"]), wp_get),
    Tool("wp_create_draft",
         "Create a new post or page as a DRAFT (never live). content is HTML using WordPress block-friendly tags "
         "(<h2>, <p>, <ul>, <a>). For posts you can pass category and tag names; missing ones are created.",
         schema({"kind": KIND, "title": S, "content": S, "excerpt": S, "slug": S,
                 "categories": {"type": "array", "items": S}, "tags": {"type": "array", "items": S}},
                ["kind", "title", "content"]), wp_create_draft),
    Tool("wp_update", "Edit a post or page's title/content/excerpt/slug. Live content needs owner approval; drafts don't. "
                      "Always wp_get first and send the COMPLETE new content, not a fragment.",
         schema({"kind": KIND, "id": I, "title": S, "content": S, "excerpt": S, "slug": S}, ["kind", "id"]), wp_update),
    Tool("wp_publish", "Publish a draft now, or schedule it (schedule_for = ISO datetime in the site's timezone). Owner must approve.",
         schema({"kind": KIND, "id": I, "schedule_for": S}, ["kind", "id"]), wp_publish),
    Tool("wp_list_categories", "List the site's post categories.", schema({}), wp_list_categories),
]
