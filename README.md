# My AI Team

Five Claude agents that work for you:

```
You
└── Team Lead (Sonnet)            plans, delegates, reports back to you
    ├── Projects & Calendar (Haiku)  Apple Calendar, projects, deadlines
    ├── Travel Agent (Haiku)          flights, hotels, itineraries (never books on its own)
    ├── Web & Content (Sonnet)        WordPress posts and pages (drafts; you approve publishing)
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

### Windows (PowerShell)

```powershell
git clone https://github.com/arpitlall20/Daniel-Corp..git Daniel-Corp   # Windows can't have a folder ending in "."
cd Daniel-Corp
py -m venv .venv
.venv\Scripts\Activate.ps1      # if blocked: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
copy .env.example .env
notepad .env
python main.py
```

## Use it

### Control panel (recommended)

```bash
python web.py          # opens http://localhost:8000 in your browser
```
On Windows you can just double-click **start.bat**.

What's in it:
- **Live team map**: an animated org chart. Watch work flow between agents, see what each is doing,
  who's waiting on you, and who hit a problem. Click anyone for their profile.
- **Chat with anyone**, with a live progress card (every step, timer, cost so far, **Stop** button)
  and **Show steps** under every reply.
- **Approvals** pop up bottom-right (with sound and desktop alerts if you turn them on 🔔);
  unanswered ones are declined after 10 minutes.
- **＋ Add team member**: name, emoji, job description (or start from a template: researcher,
  social media, email writer, finance tracker, customer support), abilities, model, and manager.
- **Drag and drop** anyone onto a new manager, on the live map or in the team list. Managers can
  delegate to their own people; moves that would make someone report to their own team are blocked.
- **Agent profiles**: what each can and won't do, change manager or model, spending, recent work;
  edit or remove members you added.
- **Connections** 🔌: see and test Claude, Apple Calendar, WordPress and GitHub.
- Activity feed with filters, task board, team memory, usage by agent and day.
- Voice input 🎤 (Chrome/Edge), read-aloud 🔊, light/dark theme, keyboard shortcuts (press **?**).

It only runs on your computer. Agents keep working if you close the browser tab; closing the
terminal window stops them.

### Terminal

```bash
python main.py                                   # chat with the Team Lead
python main.py chat assistant                    # chat with your assistant (or travel, senior_dev, junior_dev)
python main.py ask "What's on this week?"        # one-off request
python main.py ask "Find flights BNE-SYD 3 Mar" --to travel   # talk to one agent directly
python main.py status                            # who's on/off
python main.py off travel                        # switch an agent off
python main.py on all                            # switch everyone on
python main.py tasks                             # show the shared task board
```

Inside chat you can also type `switch travel` (talk to someone else), `status`, `on travel`, `off senior_dev`, `quit`.
Each member remembers your conversation with them until you quit.

## WordPress (Web & Content agent)

1. In WordPress: **Users → Profile → Application Passwords**, name it `Daniel Corp`, click Add, copy the password.
2. Add `WP_URL`, `WP_USERNAME` (your login name) and `WP_APP_PASSWORD` to `.env`.
3. Test: `python check_wordpress.py`

It writes drafts freely; publishing, scheduling, or changing anything already live asks for your approval.
It has no delete tool. Works with self-hosted WordPress and WordPress.com plans that support plugins.

## Spending

The header shows estimated spend **today / this month / all time**; click it (or the Usage tab) for a
breakdown by agent and by day. It's saved in `data/usage.json`, so it keeps counting across restarts.
Set `MONTHLY_BUDGET_USD` in `.env` to get a warning. Exact balance: console.anthropic.com > Billing.

## Your org chart

Agents you add and any changes to who reports to whom are saved in `data/org.json` on your computer.
Built-in agents can be switched off but not removed; agents you added can be edited or removed (their
team moves up to their manager). The Team Lead always reports to you.

## Memory

- **Saved chats**: every conversation is saved in `data/chats/` and continues after a restart.
  The panel opens with a clean screen each time ("Show earlier messages" to look back), but agents still
  remember. "Forget chat" erases one conversation from the agent too. Agents see your last 20 messages, which keeps costs low.
- **Team memory**: when you tell any agent a lasting fact ("I prefer morning meetings"), it saves it
  to `data/memory.md`, and every agent reads it from then on. View or edit it in the panel's Memory tab.
- Both stay on your computer; `.gitignore` keeps them out of GitHub.

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
