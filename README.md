# Instagram assistant (version 3)

The assistant reads **Instagram's own data download** to know exactly who you follow, who follows you, and every
conversation. The AI (Claude, with your API key) writes hellos and replies in your voice, sorts chats into "you'd
click", "worth a closer look" and the rest, and remembers what people told you.

**New in version 3: Claude uses the website the way you do.** Earlier versions clicked Instagram's pages by their
hidden code, which broke whenever Instagram changed it. Now, for each message or unfollow, Claude looks at a picture
of the page and clicks (assistant/eyes.py). Claude can't type and never presses Enter: the app types the exact
message you approved, checks it letter for letter in the message box, and only then sends it and checks it
appeared. This costs about 1 US cent per message or unfollow with Claude Haiku (Settings can switch to Sonnet:
steadier on unusual pages, about 10 to 20 cents each).

Also since 3.1: **Watch it work** on the Today page shows the Instagram page Claude is looking at while it sends;
**Send just one first** sends a single message and pauses so you can check it in Instagram; each chat's address is
remembered after the first visit, so later messages to the same person go straight there (fewer steps, cheaper);
and a **daily AI spending limit** (Settings, default 5 US dollars) pauses writing and sending for the day once
reached.

**Nothing is sent until you approve it.** Approving can be one tap for a whole batch ("Approve all ready ones",
"Approve all hellos"); replies the AI flags still need you one by one.

## Daily routine

1. **Evening:** request your download in Instagram (Accounts Center → Your information and permissions → Download
   your information → Messages + Followers and following, **JSON**, **All time**). When it arrives, save it to
   Downloads and press **Load from Downloads** on the dashboard. Replies are written by themselves.
2. **Morning:** check **Replies** and **Hellos**, change anything, approve, then **Send everything I approved**.
   It goes one at a time at a steady pace in the background; you can use your laptop meanwhile.

## Commands

```bash
bash setup-mac.sh     # install (copies your sign-in from ../convo-agent-old if present)
bash login.sh         # sign in to Instagram in the assistant's browser
bash check.sh         # Claude tries every website step without sending anything (about 5 cents);
                      # send Claude data/diagnostics/check.txt
bash start.sh         # start the assistant and open the dashboard
bash update.sh        # install a newer zip from Downloads, keeping your data
```

## How problems are handled

Each problem is sorted into: **try later** (Instagram didn't load: waits 10 minutes), **skip this person** (private,
gone, can't be messaged), **take a break** (Instagram said "try again later": 6 hours), or **stop and tell you**
(sign in again, the AI key or credit needs looking at, or Claude couldn't finish three times in a row). Before every reply it checks it's the right
chat, that they haven't written again since the download, and that you haven't already answered; before every
hello it checks the chat is empty. Daily numbers (messages, unfollows) and the gaps between them are in Settings.

## Costs

Hellos use your own wording and cost nothing to write. Writing each reply is one AI call: about 1 to 3 US cents.
Sending each message or unfollowing someone: about 1 cent (Claude looking at the page and clicking).

---

The rest of this file describes version 1 (`agent/`), which version 2 reuses for the browser, the voice rules and
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
