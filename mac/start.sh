#!/bin/bash
# Opens Convo from one copy of its code ($1): sets it up first if this version hasn't been set up on this Mac yet,
# then starts Convo (if it isn't running) and opens its window. Convo.app runs this through mac/Convo.
# Your data lives in ~/Library/Application Support/Convo, so new versions keep everything.
set -u
APP="$1"; shift
SUPPORT="$HOME/Library/Application Support/Convo"
LOGS="$HOME/Library/Logs/Convo"
VERSION="$(cat "$APP/VERSION")"
PY="$SUPPORT/venv/bin/python"
mkdir -p "$SUPPORT" "$LOGS"

show_problem() {
  # Something went wrong: say so in a box instead of nothing happening.
  osascript - "$1" "$LOGS" <<'OSA' >/dev/null 2>&1
on run argv
  set msg to (item 1 of argv) & return & return & "The details are in " & (item 2 of argv) & ". Send Claude a screenshot of them."
  set b to button returned of (display dialog msg buttons {"Show the details", "OK"} default button "OK" with title "Convo" with icon caution)
  if b is "Show the details" then do shell script "open " & quoted form of (item 2 of argv)
end run
OSA
}

if [ "$(cat "$SUPPORT/.installed" 2>/dev/null)" != "$VERSION" ] || [ ! -x "$PY" ]; then
  # First time (or a version that wasn't installed from inside Convo): set up in a Terminal window, so you can see
  # it working and any problem.
  if ! osascript - "$APP/mac/setup.sh" "$APP" "${CONVO_BUNDLE:-}" >>"$LOGS/launcher.log" 2>&1 <<'OSA'
on run argv
  set cmd to "bash " & quoted form of (item 1 of argv) & " " & quoted form of (item 2 of argv) & " " & quoted form of (item 3 of argv)
  tell application "Terminal"
    activate
    do script cmd
  end tell
end run
OSA
  then
    show_problem "Convo needs to open Terminal to set itself up, and your Mac didn't let it. In System Settings, Privacy & Security, Automation, turn on Terminal under Convo, then open Convo again."
  fi
  exit 0
fi

cd "$SUPPORT"
export PYTHONPATH="$APP" CONVO_LOGS="$LOGS" CONVO_HOME="$SUPPORT" PYTHONDONTWRITEBYTECODE=1
echo "--- $(date) opening Convo $VERSION from $APP" >> "$LOGS/launcher.log"
if ! "$PY" -c 'import assistant.app' >> "$LOGS/launcher.log" 2>&1; then
  show_problem "Convo $VERSION couldn't start. If this began after an update, put Convo in Applications again from GitHub (your data is kept)."
  exit 1
fi
# The window keeps this program running while it's open, so the Dock shows Convo. Problems show their own message.
exec "$PY" -m assistant app "$@" >> "$LOGS/launcher.log" 2>&1
