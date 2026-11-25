"""Human-in-the-loop approvals. Anything risky goes through ask_approval().

By default it asks in the terminal. The web panel swaps in its own handler (set_handler) so the
question pops up in your browser instead.
"""
import os
from typing import Callable


def _terminal(action: str) -> bool:
    print("\n" + "=" * 60)
    print("APPROVAL NEEDED")
    print(action)
    print("=" * 60)
    answer = input("Approve? [y/N]: ").strip().lower()
    return answer in ("y", "yes")


_handler: Callable[[str], bool] = _terminal


def set_handler(fn: Callable[[str], bool]) -> None:
    global _handler
    _handler = fn


def ask_approval(action: str) -> bool:
    """AUTO_DENY=1 refuses everything (safe for tests)."""
    if os.getenv("AUTO_DENY") == "1":
        print(f"\n[approval auto-denied] {action}")
        return False
    return _handler(action)
