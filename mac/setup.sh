#!/bin/bash
# Sets Convo up on this Mac. Convo.app runs this in Terminal the first time, and after an update.
#   - installs uv (a Python installer) if it's missing, then Python 3.12 and Convo's parts, in Convo's own folder
#   - installs the browser Convo uses for Instagram
#   - moves your data across from an older convo-agent folder, if there is one (nothing is deleted there)
# Everything goes in ~/Library/Application Support/Convo. Nothing is installed system-wide.
set -euo pipefail
APP="${1:?usage: setup.sh /path/to/convo-code [/Applications/Convo.app]}"
BUNDLE="${2:-}"
SUPPORT="$HOME/Library/Application Support/Convo"
LOGS="$HOME/Library/Logs/Convo"
VERSION="$(cat "$APP/VERSION")"
mkdir -p "$SUPPORT/data" "$LOGS"
exec > >(tee -a "$LOGS/setup.log") 2>&1

say() { printf '\n\033[1m%s\033[0m\n' "$1"; }
fail() {
  printf '\n\033[1;31mSetup stopped: %s\033[0m\n' "$1"
  echo "Nothing was changed outside $SUPPORT. The full log is in $LOGS/setup.log"
  echo "You can close this window and open Convo again to retry."
  exit 1
}
trap 'fail "a step went wrong (see the lines above)."' ERR

say "Setting up Convo $VERSION. The first time takes about 3 to 5 minutes."

# 1. uv
UV="$(command -v uv || true)"
for c in "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
  if [ -z "$UV" ] && [ -x "$c" ]; then UV="$c"; fi
done
if [ -z "$UV" ]; then
  say "Installing uv (it installs Python for Convo)"
  curl -LsSf https://astral.sh/uv/install.sh | env UV_NO_MODIFY_PATH=1 sh || fail "couldn't download uv. Check the internet connection."
  UV="$HOME/.local/bin/uv"
fi
if [ ! -x "$UV" ]; then fail "uv isn't where it should be ($UV)."; fi

# 2. Python and Convo's parts
say "Getting Python 3.12 and Convo's parts"
if [ ! -x "$SUPPORT/venv/bin/python" ] || ! "$SUPPORT/venv/bin/python" -c 'import sys; sys.exit(sys.version_info[:2] != (3, 12))'; then
  rm -rf "$SUPPORT/venv"
  "$UV" venv -q -p 3.12 "$SUPPORT/venv"
fi
"$UV" pip install -q -p "$SUPPORT/venv/bin/python" -r "$APP/requirements.lock"
shasum -a 256 "$APP/requirements.lock" | cut -d' ' -f1 > "$SUPPORT/venv/.convo-lock"   # updates skip this when unchanged

# 3. The browser
say "Installing the browser Convo uses for Instagram (about 150 MB the first time)"
"$SUPPORT/venv/bin/python" -m playwright install chromium

# 4. Your data
say "Your data"
for OLD in "$HOME/Downloads/convo-agent" "$HOME/convo-agent" "$HOME/Downloads/convo-agent-old" "$HOME/Desktop/convo-agent"; do
  if [ ! -d "$OLD/data" ]; then continue; fi
  if [ -f "$SUPPORT/.migrated" ]; then break; fi
  echo "  Found an older Convo folder at $OLD; copying your things across (the old folder is left as it is)."
  for f in assistant.db profile.json never.txt intro-list.txt dashboard-token; do
    if [ -f "$OLD/data/$f" ] && [ ! -f "$SUPPORT/data/$f" ]; then cp -p "$OLD/data/$f" "$SUPPORT/data/$f"; echo "  copied $f"; fi
  done
  if [ -d "$OLD/data/browser-profile" ] && [ ! -d "$SUPPORT/data/browser-profile" ]; then
    cp -R "$OLD/data/browser-profile" "$SUPPORT/data/browser-profile"
    rm -f "$SUPPORT/data/browser-profile"/Singleton*
    echo "  copied your Instagram sign-in"
  fi
  if [ -f "$OLD/.env" ] && [ ! -f "$SUPPORT/.env" ]; then
    grep -E '^AGENT_(LLM__|PACE__|EYES__|DAILY_AI_BUDGET_USD|REPLY_WINDOW_DAYS|ACCOUNT_USERNAME|WRITE_OVERNIGHT|SEND_AT|QUIET_)' "$OLD/.env" > "$SUPPORT/.env" || true
    chmod 600 "$SUPPORT/.env"
    echo "  copied your settings (and AI key, if you'd saved one)"
  fi
  date > "$SUPPORT/.migrated"
  break
done
for f in profile.json never.txt intro-list.txt; do
  if [ ! -f "$SUPPORT/data/$f" ] && [ -f "$APP/seed/$f" ]; then cp "$APP/seed/$f" "$SUPPORT/data/$f"; echo "  added $f"; fi
done
if [ ! -f "$SUPPORT/data/profile.json" ] && [ -f "$APP/seed/profile.example.json" ]; then
  cp "$APP/seed/profile.example.json" "$SUPPORT/data/profile.json"
  echo "  No About me was found, so Convo started a blank one. Fill it in under AI preferences, About me."
fi
if [ -f "$SUPPORT/data/assistant.db" ]; then echo "  Your conversations, people and hellos are here."; fi
if [ ! -f "$SUPPORT/.env" ]; then touch "$SUPPORT/.env"; chmod 600 "$SUPPORT/.env"; fi

# 5. A quick look that it all starts
say "Checking it starts"
cd "$SUPPORT"
PYTHONPATH="$APP" "$SUPPORT/venv/bin/python" -c 'import assistant.app, playwright; print("  all parts present")'

echo "$VERSION" > "$SUPPORT/.installed"
trap - ERR
say "All set. Opening Convo."
if [ -n "$BUNDLE" ] && [ -d "$BUNDLE" ]; then
  open "$BUNDLE"
else
  (PYTHONPATH="$APP" CONVO_LOGS="$LOGS" CONVO_HOME="$SUPPORT" "$SUPPORT/venv/bin/python" -m assistant app &)
fi
echo
echo "You can close this window. Next time, just open Convo from Applications or the Dock."
