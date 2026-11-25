# My AI Team

Five Claude agents that work for you:

```
You
└── Team Lead (Sonnet)            plans, delegates, reports back to you
    ├── Projects & Calendar (Haiku)  Apple Calendar, projects, deadlines
    ├── Travel Agent (Haiku)          flights, hotels, itineraries (never books on its own)
    └── Senior Developer (Sonnet)     designs, codes, reviews PRs on GitHub
        └── Junior Developer (Haiku)  small tasks; learns from data/lessons.md
```

## Setup (about 15 minutes)

1. **Python 3.10+** and install packages:
   ```bash
   python -m venv .venv && source .venv/bin/activate
   pip install -r requirements.txt
   ```
2. **Claude API key**: create one at console.anthropic.com. Set a monthly spend limit there too.
3. **Apple Calendar**: go to appleid.apple.com → Sign-In and Security → App-Specific Passwords → generate one.
4. **GitHub**: create a repo, clone it locally (that's `REPO_PATH`), and make a fine-grained token
   (Settings → Developer settings → Fine-grained tokens) with *Contents* and *Pull requests* read/write on that repo.
5. `cp .env.example .env` and fill it in.
6. Edit `data/travel_preferences.md` with your preferences.

## Use it

```bash
python main.py                                   # chat with the Team Lead
python main.py ask "What's on this week?"        # one-off request
python main.py ask "Find flights BNE-SYD 3 Mar" --to travel   # talk to one agent directly
python main.py status                            # who's on/off
python main.py off travel                        # switch an agent off
python main.py on all                            # switch everyone on
python main.py tasks                             # show the shared task board
```

Inside chat you can also type `status`, `on travel`, `off senior_dev`, `quit`.

## Safety rules built in

- You approve (y/N in the terminal) before: creating/deleting calendar events, merging PRs, running unusual commands.
- Nobody commits to `main`: all code goes branch → pull request → senior review → your approval.
- The travel agent never books or pays; it gives options and booking links.
- Agents that are switched off can't be delegated to.
- A cost estimate prints at the end of every session.

## Files

| Path | What it is |
|---|---|
| `team/agent.py` | The agent loop + cost tracking |
| `team/roster.py` | The five agents: instructions, tools, who reports to whom |
| `team/tools/` | Calendar, task board, GitHub/code, memory tools |
| `team_config.json` | On/off switches and which model each agent uses |
| `data/lessons.md` | What the senior has taught the junior |
| `data/team.db` | Task board and projects (SQLite) |

## Change a model

Edit `team_config.json`, e.g. set the senior dev to `claude-opus-5-5` for harder projects.
