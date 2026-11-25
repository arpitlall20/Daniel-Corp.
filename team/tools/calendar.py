"""Apple Calendar (iCloud) via CalDAV.

Setup: appleid.apple.com -> Sign-In and Security -> App-Specific Passwords -> generate one,
then put ICLOUD_EMAIL and ICLOUD_APP_PASSWORD in .env. Events sync to your iPhone and Mac.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..approvals import ask_approval
from .base import S, Tool, schema

TZ = ZoneInfo(os.getenv("TIMEZONE", "Australia/Brisbane"))
_calendar = None


def _cal():
    global _calendar
    if _calendar is None:
        import caldav  # imported lazily so the rest of the team works without it

        client = caldav.DAVClient(
            url="https://caldav.icloud.com",
            username=os.environ["ICLOUD_EMAIL"],
            password=os.environ["ICLOUD_APP_PASSWORD"],
        )
        calendars = client.principal().calendars()
        wanted = os.getenv("ICLOUD_CALENDAR_NAME", "")
        _calendar = next((c for c in calendars if c.name == wanted), calendars[0])
    return _calendar


def _parse(dt: str) -> datetime:
    d = datetime.fromisoformat(dt)
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def list_events(start: str, end: str) -> str:
    events = _cal().search(start=_parse(start), end=_parse(end), event=True, expand=True)
    lines = []
    for ev in events:
        c = ev.icalendar_component
        lines.append(f"{c.get('dtstart').dt} -> {c.get('dtend').dt} | {c.get('summary')} | uid={c.get('uid')}")
    return "\n".join(sorted(lines)) or "No events in that range."


def find_free_slots(day: str, minutes: int = 60, work_start: int = 9, work_end: int = 17) -> str:
    d = _parse(day).replace(hour=0, minute=0, second=0, microsecond=0)
    start, end = d.replace(hour=work_start), d.replace(hour=work_end)
    busy = []
    for ev in _cal().search(start=start, end=end, event=True, expand=True):
        c = ev.icalendar_component
        s, e = c.get("dtstart").dt, c.get("dtend").dt
        if isinstance(s, datetime):
            busy.append((s.astimezone(TZ), e.astimezone(TZ)))
    free, cursor = [], start
    for s, e in sorted(busy):
        if (s - cursor) >= timedelta(minutes=minutes):
            free.append(f"{cursor:%H:%M}-{s:%H:%M}")
        cursor = max(cursor, e)
    if (end - cursor) >= timedelta(minutes=minutes):
        free.append(f"{cursor:%H:%M}-{end:%H:%M}")
    return f"Free on {d:%a %d %b}: " + (", ".join(free) or "nothing that long")


def create_event(title: str, start: str, end: str, notes: str = "", location: str = "") -> str:
    if not ask_approval(f"Calendar: create '{title}' from {start} to {end} {location}"):
        return "Owner declined. Event not created."
    _cal().save_event(dtstart=_parse(start), dtend=_parse(end), summary=title,
                      description=notes, location=location)
    return f"Event '{title}' created."


def delete_event(uid: str) -> str:
    ev = _cal().event_by_uid(uid)
    title = ev.icalendar_component.get("summary")
    if not ask_approval(f"Calendar: DELETE '{title}' (uid {uid})"):
        return "Owner declined. Event kept."
    ev.delete()
    return f"Deleted '{title}'."


DT = {"type": "string", "description": "ISO datetime, e.g. 2026-12-01T09:00"}

CALENDAR_TOOLS = [
    Tool("list_events", "List calendar events between two datetimes.", schema({"start": DT, "end": DT}, ["start", "end"]), list_events),
    Tool("find_free_slots", "Find free slots on a day during working hours.",
         schema({"day": DT, "minutes": {"type": "integer"}}, ["day"]), find_free_slots),
    Tool("create_event", "Create a calendar event (owner must approve).",
         schema({"title": S, "start": DT, "end": DT, "notes": S, "location": S}, ["title", "start", "end"]), create_event),
    Tool("delete_event", "Delete an event by uid (owner must approve).", schema({"uid": S}, ["uid"]), delete_event),
]
