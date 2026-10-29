#!/usr/bin/env bash
# Start the Instagram assistant and open its dashboard, already logged in. Stop with Control + C.
set -euo pipefail
cd "$(dirname "$0")"
( for _ in $(seq 1 30); do
    sleep 1
    if [ -s data/dashboard-token ] && curl -s -o /dev/null http://127.0.0.1:8765/; then
      open "http://127.0.0.1:8765/#token=$(cat data/dashboard-token)"; exit 0
    fi
  done ) &
# caffeinate keeps the Mac awake while the app runs (the screen can still turn off).
if command -v caffeinate >/dev/null; then
  exec caffeinate -i .venv/bin/python -m assistant run
fi
exec .venv/bin/python -m assistant run
