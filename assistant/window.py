"""The app window: the dashboard in its own window (Chromium in app mode, the same browser the assistant already
installed), so Convo looks and behaves like an app rather than a browser tab. Falls back to the default browser."""
from __future__ import annotations

import json
import subprocess
import time
import urllib.request
import webbrowser
from pathlib import Path


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


def ask_to_quit(port: int, token: str) -> None:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/quit", method="POST", data=b"{}",
                                 headers={"X-Agent-Token": token, "content-type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=3).read()
    except Exception:
        pass
