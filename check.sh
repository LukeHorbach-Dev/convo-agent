#!/usr/bin/env bash
# Try every website step the assistant uses, on your account, without sending anything or unfollowing anyone.
set -uo pipefail
cd "$(dirname "$0")"
if curl -s -o /dev/null --max-time 2 http://127.0.0.1:8765/; then
  echo "The app is running. Press Control + C in its window first, then run this again."; exit 1
fi
.venv/bin/python -m assistant check
echo
echo "Send Claude the file check.txt from the folder that opens now."
open data/diagnostics 2>/dev/null || true
