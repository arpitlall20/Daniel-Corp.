"""Human-in-the-loop approvals. Anything risky goes through ask_approval()."""
import os


def ask_approval(action: str) -> bool:
    """Ask you in the terminal before a risky action. AUTO_DENY=1 refuses everything (safe for tests)."""
    if os.getenv("AUTO_DENY") == "1":
        print(f"\n[approval auto-denied] {action}")
        return False
    print("\n" + "=" * 60)
    print("APPROVAL NEEDED")
    print(action)
    print("=" * 60)
    answer = input("Approve? [y/N]: ").strip().lower()
    return answer in ("y", "yes")
