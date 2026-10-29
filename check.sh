#!/usr/bin/env bash
# Try every step on your Instagram account without sending a message or unfollowing anyone, then show the report.
set -uo pipefail
cd "$(dirname "$0")"
if curl -s -o /dev/null --max-time 2 http://127.0.0.1:8765/; then
  echo "The app is running. Press Control + C in its window first, then run this again."; exit 1
fi
AGENT_PLATFORM=instagram .venv/bin/python -m agent check
echo
echo "Send Claude the file check.txt from the folder that opens now."
open data/diagnostics 2>/dev/null || true
