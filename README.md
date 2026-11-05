# Convo, your personal brand command centre (version 5)

Convo is a Mac app. It reads **Instagram's own data download** to know exactly who you follow, who follows you and
every conversation. Claude writes hellos and replies in your voice, sorts people into "you'd click", "worth a
closer look" and the rest, and remembers what they told you. You check, approve and send, or ask it things.
Version 5 adds the **Command Centre**: one home for your accounts, conversations, hellos, analytics and growth.

## Install

Open **Terminal**, paste this line and press Enter:

    curl -fsSL https://raw.githubusercontent.com/LukeHorbach-Dev/convo-agent/main/install.sh | bash

It downloads the newest Convo, checks it, puts it in Applications, sets it up (3 to 5 minutes the first time) and
opens it. Run the same line again any time to reinstall. Your data lives in `~/Library/Application Support/Convo`,
so reinstalling or deleting the app never loses it.

## Updates

Convo looks at this project's releases and shows **"Convo x.y.z is ready"** when there's a new one. Press **Update
now** and it downloads, checks and installs it, backs up your data and restarts by itself. **Undo the last update** goes
back to the version before. Updates need a read-only GitHub key once (Convo, **Updates**, follow the four steps).

Publishing a version (for Claude or anyone working on Convo): set `__version__` in `assistant/__init__.py`, add a
`## <version>` section to `CHANGELOG.md`, commit and push to main, then push the tag `v<version>` or run the
**release** workflow (Actions, Run workflow) which makes the tag itself.
The release workflow (`.github/workflows/release.yml`) runs the tests, builds Convo.app and publishes the release.
Versions with a dash (`5.1.0-preview.1`) are previews.

## A day with Convo

1. **Evening:** request your download in Instagram (Accounts Center, Your information and permissions, Download
   your information: Messages + Followers and following, **JSON**, **All time**). Save it in Downloads and press
   **Load** on Today. Replies are written **overnight at half price** (Anthropic's batch service; usually within
   the hour).
2. **Morning:** check **Replies** and **Hellos**, approve (one by one or "Approve all ready"), then **Send**, or
   **Send just one first** to watch it in the live view. Or set **Send by itself at** in Settings.

## What's in it

| | |
|---|---|
| **Stop now** | The red button stops everything at once: Claude stops at its next click, writing stops. |
| **Ask (⌘K)** | "What did @maya say?", "who's interesting this week?", "write a reply to @tom about sunday", "send 'see you at 6' to @ben", "stop". Simple commands are free. |
| **Person panel** | Click anyone: their whole chat, what Convo remembers, your note, an AI report (about 1¢), message them, never-message, unfollow. |
| **Briefing** | Today shows new messages, who's waiting, people you'd click with and chats worth a closer look. |
| **Watch it work** | While sending, Today shows the Instagram page Claude is looking at. |
| **Schedules** | Send at a set time each day; quiet hours when nothing is sent. |
| **Spending limit** | Writing and sending pause for the day at your limit (default $5). Spend is broken down in Settings. |
| **Practice** | Settings, Practice: a pretend Instagram with made-up people to try everything, including an **automatic** switch that approves and sends by itself. Only practice has that switch. |
| **Spreadsheet** | People, Spreadsheet: everyone with summaries, interest, concerns, notes. |
| **Self-check** | Settings: Claude tries every Instagram step on your account without sending anything (about 5¢). |

## How sending works (and why it's reliable)

Claude looks at a picture of the page and clicks, like a person (assistant/eyes.py), so Instagram changing its
pages doesn't break anything. Claude can't type and never presses Enter: the app types the exact approved text,
checks it letter for letter in the message box, sends it, and checks it appeared. Before a reply it checks it's
the right person and that they haven't written again; before a hello, that the chat is empty. Each chat's address
is remembered, so later messages go straight there. Problems are sorted into: try later, skip this person, take a
break (Instagram said slow down: 6 hours), or stop and tell you.

## Costs (US cents)

| | Opus (default) | Sonnet | Haiku |
|---|---|---|---|
| Write a reply overnight | about 1¢ | about 0.6¢ | under 0.1¢ |
| Write a reply straight away | about 2¢ | about 1¢ | about 0.1¢ |
| Send a message / unfollow (Claude Haiku looking) | about 1¢ | | |
| Hellos | free to write (your own words), about 1¢ to send | | |

At $5 a day that's roughly 150 to 250 messages written and sent.

## For developers

```bash
uv venv -p 3.12 .venv && uv pip install -p .venv/bin/python -r requirements.lock && uv pip install -p .venv/bin/python -e . --no-deps
.venv/bin/playwright install chromium
.venv/bin/python -m pytest -q          # about 300 tests, 5 minutes (real Chromium against the practice site)
.venv/bin/python -m assistant run      # the app in this Terminal (dashboard on http://127.0.0.1:8765)
.venv/bin/python -m assistant practice # the practice copy (port 8766); --real-ai to use real Claude
python3 mac/make_icon.py               # the app icon (needs Pillow)
python3 mac/build_app.py               # dist/Convo.app and dist/Convo-<version>.zip
```

Layout: `assistant/` is the app (export.py reads downloads, brain.py and writer.py write replies, eyes.py uses
Instagram, sender.py works through what's approved, ask.py is the Ask box, reports.py, practice.py, app.py is the
dashboard API, web/app.html the dashboard). `agent/` is version 1, reused for the browser supervisor, voice rules
and AI client. `mac/` is the app bundle.

---

The rest of this file describes version 1 (`agent/`), which later versions reuse for the browser, the voice rules and
the AI connection.

# Conversation Agent

A local, single-user conversation agent. It notices new followers and incoming messages, drafts replies in your
own style from a profile of verified facts, and delivers them through a durable outbox, with state machines,
recovery, reconciliation, and a local dashboard.

**On a real platform (Instagram) every outgoing message needs your approval in the dashboard.** This is enforced
in code (`agent/core/policy.py: assert_deliverable`) and is not configurable. Against the mock platforms the full
loop runs autonomously so it can be tested end to end.

## Setup on a Mac (Apple Silicon or Intel)

You need four things. Git is optional.

1. **Python 3.12**: download the macOS installer from python.org (3.12.x), or `brew install python@3.12`.
2. **uv** (installs the pinned dependencies): in Terminal, `curl -LsSf https://astral.sh/uv/install.sh | sh`,
   then open a new Terminal window.
3. **This project folder**: either a zip of `convo-agent/` or `git clone` from a repo (Git comes with Xcode
   command line tools: `xcode-select --install`).
4. **For real replies (optional)**: an Anthropic API key with credit from console.anthropic.com. Without it the app
   still runs; introductions use your own templates, and reply drafts are canned test text.

Then, in Terminal:

```bash
cd ~/Downloads/convo-agent                                    # wherever the folder is
uv venv -p 3.12 .venv
uv pip install -p .venv/bin/python -r requirements.lock      # pinned dependencies
uv pip install -p .venv/bin/python -e . --no-deps            # the app itself
.venv/bin/playwright install chromium                         # the browser the agent drives (about 150 MB)
cp .env.example .env                                          # then edit (see docs/CONFIGURATION.md)
```

**Fresh reinstall** (keeps your personal files and Instagram sign-in): rename the old folder to
`convo-agent-old`, put the new `convo-agent` folder next to it, then run `bash setup-mac.sh` inside the new one.
It copies your files across, installs everything, and runs the tests.

`data/profile.json` (your facts, writing samples, intro templates), `data/never.txt` (never message these people)
and `data/intro-list.txt` (people to introduce yourself to) are personal and git-ignored. Start from
`data/profile.example.json` if they are missing.

## Run

**Updating:** download the new `convo-agent.zip` into Downloads, stop the app, then `bash update.sh` in this folder.
It keeps your sign-in, lists, history and settings, and prints the new version. Problems found on the live account
and how each is guarded against: [docs/LIVE_ISSUES.md](docs/LIVE_ISSUES.md).

**If something gets stuck:** stop the app and run `bash check.sh`. It tries every step on your account without
sending anything or unfollowing anyone, and writes `data/diagnostics/check.txt` to send to Claude.

**Unfollowing:** the dashboard's "People you follow" list has Unfollow selected and Unfollow everyone (type
UNFOLLOW EVERYONE to confirm). At most `AGENT_LIMITS__DAILY_UNFOLLOW_LIMIT` (50) per 24 hours; unsent hellos to
those people are cancelled.

**Replies in your style:** paste an Anthropic API key in the dashboard (Settings, "Replies in your style"). It's
saved in `.env` on your Mac only. Replies then follow the `voice` guide in `data/profile.json`, your rules, and what
you've written in your own chats, and a final pass applies your punctuation (space before ?, no apostrophes, no !,
no emoji). Without a key, reply boxes are left empty for you to type.

**Cost:** hellos come from your own templates and cost nothing. A reply is one call to `claude-opus-5-5` at low
effort: about 3,500 tokens in and 300 to 600 out, roughly 1 to 3 US cents, less once the fixed instructions are
cached. The dashboard shows a running estimate. Set `AGENT_LLM__MODEL=claude-sonnet-5-5` in `.env` to halve it.

**Not watching:** the dashboard doesn't need to be open. `start.sh` keeps the Mac awake (`caffeinate`), a Mac
notification appears when messages are waiting or the app pauses, and Settings can tuck the Instagram window
into the Dock; it keeps working there.

**Everyday use:** `bash start.sh`. It starts the agent on Instagram and opens the dashboard, already logged in.
Stop with Control + C.

**Who it says hello to** is set by `AGENT_POLICY__AUDIENCE` in `.env`: `following` (the default in `.env.example`)
means everyone you follow gets one hello, unless you already have a conversation with them; `followers` means only
people who follow you after the first run. Either way every message waits for you to press Send, at most
`AGENT_LIMITS__DAILY_SEND_LIMIT` (25) go out per 24 hours, and only `AGENT_LIMITS__MAX_WAITING_INTROS` (10) hellos
are prepared at a time. On Instagram the list is re-read every 30 minutes; messages are checked every 30 seconds.

```bash
.venv/bin/python -m agent demo                       # deterministic mock run with faults; prints a report; exit 1 if an invariant fails
.venv/bin/python -m agent run                        # mock platform, autonomous, dashboard on http://127.0.0.1:8765/
.venv/bin/python -m agent run --platform mock_web    # same, but through a real Chromium against a local mock site
.venv/bin/python -m agent login                      # open the agent's Chrome profile and sign in to Instagram by hand
AGENT_PLATFORM=instagram AGENT_MODE=draft .venv/bin/python -m agent run      # Instagram, drafts only (first trial)
.venv/bin/python -m agent import-list data/intro-list.txt                    # queue introductions for your list
AGENT_PLATFORM=instagram .venv/bin/python -m agent run                       # Instagram, every message needs your approval
.venv/bin/python -m agent diagnose                   # sanitized diagnostic bundle (no secrets, no message text)
```

The dashboard asks for the token in `data/dashboard-token` (created on first run, mode 0600). Drafts show what the
other person said in plain English, the language they wrote in, what your reply means, and 🔊 buttons that read
text aloud. **A-** and **A+** change the text size.

Stop with Ctrl+C or SIGTERM: the worker finishes its current step, the browser closes, and exit status is 0
(2 if the worker didn't stop within 30 s). The dashboard's **Emergency stop** blocks every consequential action
immediately and survives restarts until you clear it.

## Test

```bash
.venv/bin/python -m pytest                      # everything (about 2 minutes, 249 tests)
.venv/bin/python -m pytest -m "not browser"     # skip the real-Chromium tests
CHAOS_SEEDS=300 .venv/bin/python -m pytest tests/test_chaos.py   # longer randomized fault run
```

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | modules, data flow, data model, design decisions |
| [docs/STATE_MACHINES.md](docs/STATE_MACHINES.md) | job, outbox and browser state diagrams |
| [docs/RECOVERY.md](docs/RECOVERY.md) | error categories, recovery decision table, delivery guarantees |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | every setting |
| [docs/OPERATIONS.md](docs/OPERATIONS.md) | troubleshooting, crash recovery, shutdown |
| [docs/SECURITY.md](docs/SECURITY.md) | security review, data handling, retention |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | what is unavailable or unverified |
| [docs/TEST_REPORT.md](docs/TEST_REPORT.md) | what the tests cover and the latest results |
| [docs/REVIEW.md](docs/REVIEW.md) | release-readiness review: findings, fixes, coverage limits, decision |
| [REPORT.md](REPORT.md) | implementation report: implemented, tested, simulated, unavailable |
