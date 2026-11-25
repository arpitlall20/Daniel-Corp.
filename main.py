"""Talk to your AI team.

  python main.py                      # chat with the team lead
  python main.py chat assistant       # chat with one member (assistant, travel, senior_dev, junior_dev)
  python main.py ask "plan my week"   # one request to the team lead
  python main.py ask "..." --to travel  # talk to one member directly
  python main.py status               # who is on / off
  python main.py on travel            # switch a member on   (or: all)
  python main.py off senior_dev       # switch a member off  (or: all)
  python main.py tasks                # show the task board
"""
import sys

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from team.agent import cost_report  # noqa: E402
from team.config import load, set_enabled  # noqa: E402
from team.tools.tasks import list_tasks  # noqa: E402


def status() -> str:
    rows = [f"  {k:<12} {'ON ' if v['enabled'] else 'OFF'}  {v['model']}" for k, v in load()["agents"].items()]
    return "Team status:\n" + "\n".join(rows)


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "chat"

    if cmd == "status":
        print(status()); return
    if cmd in ("on", "off") and len(argv) > 1:
        print(set_enabled(argv[1], cmd == "on")); return
    if cmd == "tasks":
        print(list_tasks()); return

    from team.roster import all_agents, build_team
    agents = all_agents(build_team())

    if cmd == "ask" and len(argv) > 1:
        target = argv[argv.index("--to") + 1] if "--to" in argv else "team_lead"
        print(agents[target].run(argv[1]))
        print("\n" + cost_report()); return

    # interactive chat; each member keeps their own conversation history
    current = argv[1] if cmd == "chat" and len(argv) > 1 else ("team_lead" if cmd == "chat" else cmd)
    if current not in agents:
        print(f"Unknown member '{current}'. Choose: {', '.join(agents)}"); return
    histories: dict[str, list] = {k: [] for k in agents}
    print(status() + f"\nChatting with: {agents[current].name}"
          "\nCommands: 'switch <member>', 'status', 'on X', 'off X', 'quit'.\n")
    while True:
        try:
            msg = input(f"you -> {current}> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not msg:
            continue
        words = msg.split()
        if msg in ("quit", "exit"):
            break
        if msg == "status":
            print(status()); continue
        if words[0] in ("on", "off") and len(words) == 2:
            print(set_enabled(words[1], words[0] == "on")); continue
        if words[0] == "switch" and len(words) == 2:
            if words[1] in agents:
                current = words[1]
                print(f"Now chatting with: {agents[current].name}\n")
            else:
                print(f"Unknown member. Choose: {', '.join(agents)}")
            continue
        print(f"\n{agents[current].name}> " + agents[current].run(msg, histories[current]) + "\n")
    print(cost_report())


if __name__ == "__main__":
    main(sys.argv[1:])
