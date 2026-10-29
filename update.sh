#!/usr/bin/env bash
# Update this folder from the newest convo-agent zip in Downloads. Keeps your data (sign-in, profile, lists,
# history) and your .env settings; adds any new settings from .env.example.
#   bash update.sh                 (uses the newest ~/Downloads/convo-agent*.zip)
#   bash update.sh path/to/zip     (uses that zip)
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"

if curl -s -o /dev/null --max-time 2 http://127.0.0.1:8765/; then
  echo "The app is still running. Press Control + C in its window first, then run this again."; exit 1
fi

ZIP="${1:-}"
if [ -z "$ZIP" ]; then
  ZIP="$(ls -t "$HOME"/Downloads/convo-agent*.zip 2>/dev/null | head -1 || true)"
fi
[ -n "$ZIP" ] && [ -f "$ZIP" ] || { echo "No convo-agent zip found in Downloads. Download it from the chat first."; exit 1; }

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
unzip -q "$ZIP" -d "$TMP"
NEW="$TMP/convo-agent"
[ -f "$NEW/agent/__init__.py" ] || { echo "$ZIP doesn't look like a convo-agent zip."; exit 1; }

ver() { sed -n 's/^__version__ = "\(.*\)"/\1/p' "$1/agent/__init__.py"; }
OLD_V="$(ver "$HERE" 2>/dev/null || echo unknown)"
NEW_V="$(ver "$NEW")"
echo "Updating from version ${OLD_V:-unknown} to $NEW_V (from $(basename "$ZIP"))"

# Code and docs are replaced; data/ is left alone except the example profile.
for d in agent tests docs; do rm -rf "$HERE/$d"; cp -R "$NEW/$d" "$HERE/$d"; done
for f in pyproject.toml requirements.lock README.md REPORT.md .env.example .gitignore setup-mac.sh start.sh update.sh check.sh; do
  [ -f "$NEW/$f" ] && cp "$NEW/$f" "$HERE/$f"
done
mkdir -p "$HERE/data"
cp "$NEW/data/profile.example.json" "$HERE/data/" 2>/dev/null || true
for f in profile.json never.txt intro-list.txt; do       # only if you don't have them yet
  [ -f "$HERE/data/$f" ] || { [ -f "$NEW/data/$f" ] && cp "$NEW/data/$f" "$HERE/data/$f" && echo "  added data/$f"; } || true
done

# Settings: keep yours, add new ones.
touch .env
while IFS= read -r line; do
  key="${line%%=*}"
  case "$line" in ''|\#*) continue;; esac
  grep -q "^$key=" .env || { echo "$line" >> .env; echo "  new setting: $key"; }
done < .env.example

uv pip install -q -p .venv/bin/python -r requirements.lock
uv pip install -q -p .venv/bin/python -e . --no-deps
echo
echo "Done: $(.venv/bin/python -m agent --version). Start it with: bash start.sh"
