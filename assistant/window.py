"""The app window. On a Mac, Convo opens in its own native window (WebKit, through pywebview), with Convo's name
and icon in the Dock. If that can't start, it uses Chromium in app mode (the browser Convo already installed for
Instagram), and failing that the default browser."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path
from typing import Callable

BACKGROUND = "#0a0b0f"          # the Command Centre's background, so the window never flashes white


def chromium_path() -> str | None:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            exe = p.chromium.executable_path
        return exe if exe and Path(exe).exists() else None
    except Exception:
        return None


def open_window(url: str, profile_dir: Path) -> str:
    """Open the dashboard. Returns 'window' or 'browser'."""
    exe = chromium_path()
    if exe:
        profile_dir.mkdir(parents=True, exist_ok=True)
        subprocess.Popen([exe, f"--app={url}", f"--user-data-dir={profile_dir}", "--no-first-run",
                          "--no-default-browser-check", "--window-size=1320,900", "--disable-features=Translate"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return "window"
    webbrowser.open(url)
    return "browser"


def health(port: int, timeout_s: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=timeout_s) as r:
            data = json.loads(r.read().decode())
            return data if data.get("app") == "convo" else None
    except Exception:
        return None


def wait_until(port: int, up: bool, timeout_s: float = 40) -> bool:
    end = time.time() + timeout_s
    while time.time() < end:
        if (health(port) is not None) == up:
            return True
        time.sleep(0.4)
    return False


def token_ok(port: int, token: str) -> bool:
    """Whether the Convo on this port knows this password (an older one left running may not)."""
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/updates", headers={"X-Agent-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status == 200
    except Exception:
        return False


def stop_listener(port: int) -> list[int]:
    """Stop whatever Convo is listening on this port when it won't quit when asked (it doesn't know the current
    password, e.g. one left running from before Convo's folder was deleted). Only called after /api/health said
    it's Convo. Returns the process ids stopped."""
    import signal
    try:
        out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True,
                             timeout=10).stdout
    except Exception:
        return []
    pids = [int(p) for p in out.split() if p.isdigit() and int(p) != os.getpid()]
    for sig in (signal.SIGTERM, signal.SIGKILL):
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass
        if wait_until(port, up=False, timeout_s=10):
            break
    return pids


def ask_to_quit(port: int, token: str) -> None:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/quit", method="POST", data=b"{}",
                                 headers={"X-Agent-Token": token, "content-type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass


def show_problem(message: str, logs: Path) -> None:
    """A Mac dialog box, so a problem is never just nothing happening."""
    if sys.platform != "darwin":
        print(message, file=sys.stderr)
        return
    script = ('on run argv\n'
              'set msg to (item 1 of argv) & return & return & "The details are in " & (item 2 of argv) & ". Send Claude a screenshot of them."\n'
              'set b to button returned of (display dialog msg buttons {"Show the details", "OK"} default button "OK" '
              'with title "Convo" with icon caution)\n'
              'if b is "Show the details" then do shell script "open " & quoted form of (item 2 of argv)\n'
              'end run')
    subprocess.run(["osascript", "-", message, str(logs)], input=script, text=True, capture_output=True)


def app_icon() -> str | None:
    bundle = os.environ.get("CONVO_BUNDLE")
    for p in ((Path(bundle) / "Contents" / "Resources" / "AppIcon.icns") if bundle else None,
              Path(__file__).resolve().parent.parent / "mac" / "icon.png"):
        if p and p.exists():
            return str(p)
    return None


class Bridge:
    """What the page can ask of the window (window.pywebview.api in the page)."""

    def __init__(self):
        self._window = None          # private: pywebview only shows the page public methods

    def close(self) -> None:
        if self._window is not None:
            self._window.destroy()


def native_window(url: str, storage: Path, on_shown: Callable[[], None] = lambda: None) -> None:
    """Convo in a native Mac window. Blocks until the window is closed. Raises if pywebview can't be used."""
    import webview  # noqa: F401  (ImportError here means: use Chromium instead)
    try:                                          # call the app "Convo" in the menu bar and Dock, not "Python"
        from Foundation import NSBundle
        info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = "Convo"
    except Exception:
        pass
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    bridge = Bridge()
    win = webview.create_window("Convo", url, js_api=bridge, width=1320, height=900, min_size=(380, 560),
                                background_color=BACKGROUND, text_select=True, zoomable=True)
    bridge._window = win
    win.events.loaded += lambda: on_shown()
    storage.mkdir(parents=True, exist_ok=True)
    webview.start(private_mode=False, storage_path=str(storage), icon=app_icon())


def window_mode(data_dir: Path, version: str) -> str:
    """'native' on a Mac unless the native window failed on this version before (then 'chromium')."""
    forced = os.environ.get("CONVO_WINDOW")
    if forced in ("native", "chromium", "browser"):
        return forced
    if sys.platform != "darwin":
        return "chromium"
    failed = data_dir / "window" / "native-failed"
    trying = data_dir / "window" / "native-trying"
    if trying.exists():                       # the last native window died before it showed: don't try it again
        failed.parent.mkdir(parents=True, exist_ok=True)
        failed.write_text(trying.read_text())
        trying.unlink(missing_ok=True)
    try:
        return "chromium" if failed.read_text().strip() == version else "native"
    except OSError:
        return "native"
