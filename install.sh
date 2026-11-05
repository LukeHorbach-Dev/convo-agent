#!/bin/bash
# Installs Convo on this Mac, or replaces it with the newest version. Paste this one line into Terminal:
#
#   curl -fsSL https://raw.githubusercontent.com/LukeHorbach-Dev/convo-agent/main/install.sh | bash
#
# What it does: downloads the newest Convo from this project's GitHub releases, checks it against the release's
# checksums, puts Convo.app in Applications, sets it up (Python and Convo's parts, all inside
# ~/Library/Application Support/Convo), and opens it. Your data, sign-in and settings there are kept.
# Nothing is installed system-wide, and it never asks for your password.
set -euo pipefail

main() {
REPO="LukeHorbach-Dev/convo-agent"
API="${CONVO_GITHUB_API:-https://api.github.com}"
APPS="${CONVO_APPS:-/Applications}"
bold() { printf '\n\033[1m%s\033[0m\n' "$1"; }
fail() { printf '\n\033[1;31mConvo was not installed: %s\033[0m\n' "$1"; exit 1; }

bold "Installing Convo"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# The newest finished release, or (before there is one) the newest preview.
get() { curl -fsSL -H "Accept: application/vnd.github+json" -H "User-Agent: Convo-installer" "$@"; }
if ! get "$API/repos/$REPO/releases/latest" -o "$TMP/release.json" 2>/dev/null; then
  get "$API/repos/$REPO/releases?per_page=1" -o "$TMP/release.json" \
    || fail "couldn't reach GitHub. Check the internet connection and try again."
fi
url_of() { grep -o "\"browser_download_url\": *\"[^\"]*/$1\"" "$TMP/release.json" | head -1 | sed 's/.*"\(http[^"]*\)"$/\1/'; }
ZIP_URL="$(url_of 'Convo-[^"/]*\.zip')"
SUMS_URL="$(url_of 'SHA256SUMS\.txt')"
[ -n "$ZIP_URL" ] && [ -n "$SUMS_URL" ] || fail "the newest release on GitHub is missing its files."
ZIP="$(basename "$ZIP_URL")"
echo "  $ZIP"

get "$SUMS_URL" -o "$TMP/SHA256SUMS.txt" || fail "couldn't download the checksums."
get "$ZIP_URL" -o "$TMP/$ZIP" || fail "the download stopped part way. Try again."
WANT="$(grep " $ZIP\$" "$TMP/SHA256SUMS.txt" | cut -d' ' -f1)"
HAVE="$(shasum -a 256 "$TMP/$ZIP" | cut -d' ' -f1)"
[ -n "$WANT" ] && [ "$WANT" = "$HAVE" ] || fail "the download didn't match GitHub's checksum. Try again."
echo "  downloaded and checked"

mkdir -p "$TMP/unzipped"
if command -v ditto >/dev/null; then ditto -xk "$TMP/$ZIP" "$TMP/unzipped"; else unzip -q "$TMP/$ZIP" -d "$TMP/unzipped"; fi
[ -x "$TMP/unzipped/Convo.app/Contents/MacOS/Convo" ] || fail "the download doesn't contain Convo.app."

if [ ! -w "$APPS" ]; then APPS="$HOME/Applications"; mkdir -p "$APPS"; fi
rm -rf "$APPS/Convo.app"
mv "$TMP/unzipped/Convo.app" "$APPS/Convo.app"
xattr -cr "$APPS/Convo.app" 2>/dev/null || true          # downloaded here, so the Mac has nothing to block anyway
echo "  Convo is in $APPS"

# Set up (the same steps Convo would otherwise open a Terminal window for), then open it.
CODE="$APPS/Convo.app/Contents/Resources/app"
SUPPORT="$HOME/Library/Application Support/Convo"
mkdir -p "$SUPPORT"
cat "$CODE/VERSION" > "$SUPPORT/bundled"
rm -f "$SUPPORT/current"
bash "$CODE/mac/setup.sh" "$CODE" "$APPS/Convo.app" < /dev/null
}

main "$@"      # (all in one function, so a cut-off download never runs half a script)
