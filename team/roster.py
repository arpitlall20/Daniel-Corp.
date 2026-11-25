"""Builds the five-agent team and wires up who reports to whom."""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from .agent import Agent
from .config import model_for
from .tools.calendar import CALENDAR_TOOLS
from .tools.code import DEV_TOOLS, REVIEW_TOOLS
from .tools.memory import LESSONS, LESSON_TOOLS, TRAVEL_MEMORY_TOOLS, read_text
from .tools.tasks import PROJECT_TOOLS, TASK_TOOLS

OWNER = os.getenv("OWNER_NAME", "the owner")
TZ = os.getenv("TIMEZONE", "Australia/Brisbane")

WEB_SEARCH = {"type": "web_search_20250305", "name": "web_search", "max_uses": 8}


def now_context() -> str:
    return f"Current date/time: {datetime.now(ZoneInfo(TZ)):%A %d %B %Y, %H:%M} ({TZ})."


def junior_context() -> str:
    lessons = read_text(LESSONS) or "(none yet)"
    return now_context() + "\n\nLessons from your senior developer (follow these):\n" + lessons


COMMON = f"""You are part of {OWNER}'s AI team. Be concise and factual.
Never invent results: if a tool fails, say so. Anything that spends money, sends messages to other
people, deletes things or merges code needs {OWNER}'s approval (the tools ask for it)."""


def build_team() -> Agent:
    junior = Agent(
        key="junior_dev", name="Junior Developer", model=model_for("junior_dev"),
        instructions=COMMON + """
Role: junior developer. You handle small, well-defined tasks: bug fixes, tests, docs, small refactors.
Always work on a new branch (e.g. junior/<short-name>), run the tests, commit, push and open a pull request.
Never merge. If a task is bigger or unclear, stop and say so instead of guessing.
Report back: what you changed, the test result, and the PR number.""",
        tools=list(DEV_TOOLS), extra_context=junior_context,
    )

    senior = Agent(
        key="senior_dev", name="Senior Developer", model=model_for("senior_dev"),
        instructions=COMMON + """
Role: senior developer. You plan features, write the hard code and review the junior's work.
- Give small, self-contained tasks to the junior via `delegate` (member: junior_dev) with clear acceptance criteria.
- Review every junior PR with get_pull_request_diff. Comment with feedback. If you spot a mistake
  pattern, save it with add_lesson so the junior learns.
- Your own work: new branch (senior/<name>), tests, PR.
- Only call merge_pull_request when the work is reviewed and tests pass; the owner approves the merge.
Report back: what was done, PR links, any risks.""",
        tools=list(DEV_TOOLS) + REVIEW_TOOLS + LESSON_TOOLS, team={"junior_dev": junior},
        extra_context=now_context,
    )

    assistant = Agent(
        key="assistant", name="Projects & Calendar Assistant", model=model_for("assistant"),
        instructions=COMMON + """
Role: projects and calendar assistant. You manage the owner's Apple Calendar, project list and deadlines.
Check free slots before proposing times. Keep the project list up to date. When asked for a briefing,
list today's events, upcoming deadlines (next 7 days) and overdue tasks.""",
        tools=CALENDAR_TOOLS + PROJECT_TOOLS + TASK_TOOLS, extra_context=now_context,
    )

    travel = Agent(
        key="travel", name="Travel Agent", model=model_for("travel"),
        instructions=COMMON + """
Role: travel agent. Plan trips: flights, hotels, ground transport, day-by-day itinerary, visa/passport
needs, weather and a cost estimate. Always read the saved travel preferences first, and check the
calendar for clashes. Give 2-3 options with prices and links from your web searches, and say how
recent each price is. You NEVER book or pay; you recommend and give booking links.""",
        tools=TRAVEL_MEMORY_TOOLS + [t for t in CALENDAR_TOOLS if t.name in ("list_events", "find_free_slots")],
        server_tools=[WEB_SEARCH], extra_context=now_context,
    )

    lead = Agent(
        key="team_lead", name="Team Lead", model=model_for("team_lead"),
        instructions=COMMON + f"""
Role: team lead. {OWNER} gives you requests. You:
1. Break the request into tasks and record them on the task board (add_task).
2. Delegate each task to the right member: assistant (calendar/projects), travel (trips),
   senior_dev (anything code; the senior manages the junior).
3. Coordinate: e.g. after a trip is approved, ask the assistant to block travel time.
4. Check the results, update the task board, and report back to {OWNER} in a short summary:
   done / needs your approval / blocked. Do the hands-on work yourself only for trivial things.""",
        tools=TASK_TOOLS + PROJECT_TOOLS,
        team={"assistant": assistant, "travel": travel, "senior_dev": senior},
        extra_context=now_context, max_turns=30,
    )
    return lead


def all_agents(lead: Agent) -> dict[str, Agent]:
    """Flatten the org chart so you can also talk to any member directly."""
    out, stack = {}, [lead]
    while stack:
        a = stack.pop()
        out[a.key] = a
        stack.extend(a.team.values())
    return out
