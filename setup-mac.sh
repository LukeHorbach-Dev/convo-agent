#!/usr/bin/env bash
# Fresh install on a Mac. Run from inside the new convo-agent folder:
#   bash setup-mac.sh                      (copies your personal files from ../convo-agent-old if it exists)
#   bash setup-mac.sh /path/to/old-folder  (copies them from somewhere else)
# Needs Python 3.12 and uv already installed (see README).
set -euo pipefail
cd "$(dirname "$0")"
OLD="${1:-../convo-agent-old}"

step() { printf '\n== %s\n' "$1"; }

command -v uv >/dev/null || { echo "uv is not installed or not on PATH. Open a new Terminal window and try again."; exit 1; }

if [ -d "$OLD" ]; then
  step "Copying your personal files from $OLD"
  mkdir -p data
  for f in data/profile.json data/never.txt data/intro-list.txt data/dashboard-token; do
    if [ -f "$OLD/$f" ]; then cp -p "$OLD/$f" "$f"; echo "  copied $f"; else echo "  (no $f in the old folder)"; fi
  done
  if [ -d "$OLD/data/browser-profile" ]; then
    cp -R "$OLD/data/browser-profile" data/browser-profile
    rm -f data/browser-profile/Singleton*          # stale "in use" markers from the old copy
    echo "  copied your Instagram sign-in (data/browser-profile)"
  fi
else
  step "No old folder at $OLD, so nothing to copy"
fi
# Settings always start from the current .env.example (new options appear there). Your old .env, if any, is
# left in the old folder; copy any API key across by hand.
cp .env.example .env; echo "  created .env from .env.example"

step "Creating the Python environment"
rm -rf .venv
uv venv -p 3.12 .venv
uv pip install -p .venv/bin/python -r requirements.lock
uv pip install -p .venv/bin/python -e . --no-deps

step "Installing the browser the agent uses"
.venv/bin/playwright install chromium

step "Running the tests (about a minute)"
.venv/bin/python -m pytest -q

step "All done"
[ -f data/profile.json ] || echo "Note: data/profile.json is missing. Copy it in, or start from data/profile.example.json."
[ -d data/browser-profile ] || echo "Note: not signed in yet. Run: .venv/bin/python -m agent login"
