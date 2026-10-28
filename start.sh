#!/usr/bin/env bash
# Start the agent on Instagram (every message needs your OK) and open the dashboard, already logged in.
# Stop with Control + C.
set -euo pipefail
cd "$(dirname "$0")"
( for _ in $(seq 1 30); do
    sleep 1
    if [ -s data/dashboard-token ] && curl -s -o /dev/null http://127.0.0.1:8765/; then
      open "http://127.0.0.1:8765/#token=$(cat data/dashboard-token)"; exit 0
    fi
  done ) &
AGENT_PLATFORM=instagram exec .venv/bin/python -m agent run
