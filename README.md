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

`data/profile.json` (your facts, writing samples, intro templates), `data/never.txt` (never message these people)
and `data/intro-list.txt` (people to introduce yourself to) are personal and git-ignored. Start from
`data/profile.example.json` if they are missing.

## Run

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
.venv/bin/python -m pytest                      # everything (about 1 minute, 218 tests)
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
