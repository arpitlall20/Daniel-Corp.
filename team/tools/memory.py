"""Long-term memory kept in plain markdown files you can read and edit yourself."""
from datetime import date
from pathlib import Path

from .base import S, Tool, schema

DATA = Path(__file__).resolve().parents[2] / "data"
LESSONS = DATA / "lessons.md"
TRAVEL_PREFS = DATA / "travel_preferences.md"
MEMORY = DATA / "memory.md"   # shared by the whole team; stays on your computer (not pushed to GitHub)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def add_lesson(lesson: str) -> str:
    """Senior dev records a lesson; it's loaded into the junior's instructions every run."""
    DATA.mkdir(exist_ok=True)
    with LESSONS.open("a", encoding="utf-8") as f:
        f.write(f"- ({date.today()}) {lesson.strip()}\n")
    return "Lesson saved for the junior developer."


def read_travel_preferences() -> str:
    return read_text(TRAVEL_PREFS) or "No travel preferences saved yet."


def update_travel_preferences(note: str) -> str:
    DATA.mkdir(exist_ok=True)
    with TRAVEL_PREFS.open("a", encoding="utf-8") as f:
        f.write(f"- {note.strip()}\n")
    return "Preference saved."


LESSON_TOOLS = [
    Tool("add_lesson", "Record a short, reusable lesson for the junior developer after reviewing their work.",
         schema({"lesson": S}, ["lesson"]), add_lesson),
]

TRAVEL_MEMORY_TOOLS = [
    Tool("read_travel_preferences", "Read the owner's saved travel preferences.", schema({}), read_travel_preferences),
    Tool("update_travel_preferences", "Save a new travel preference the owner mentioned.",
         schema({"note": S}, ["note"]), update_travel_preferences),
]


def remember(fact: str) -> str:
    """Save a lasting fact about the owner so every agent knows it from now on."""
    DATA.mkdir(exist_ok=True)
    existing = read_text(MEMORY)
    if fact.strip().lower() in existing.lower():
        return "Already remembered."
    with MEMORY.open("a", encoding="utf-8") as f:
        f.write(f"- {fact.strip()}\n")
    return "Saved to team memory."


def memory_text() -> str:
    return read_text(MEMORY).strip()


MEMORY_TOOLS = [
    Tool("remember",
         "Save a lasting fact or preference the owner told you (people, preferences, routines, goals, "
         "important details) so the whole team remembers it in future conversations. One short fact per call. "
         "Don't save passwords, card numbers or one-off details.",
         schema({"fact": S}, ["fact"]), remember),
]
