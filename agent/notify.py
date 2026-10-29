"""Mac notifications, so the dashboard doesn't have to be open: a banner when new messages are waiting for your
OK, when something needs your help, or when the app pauses (e.g. Instagram wants you to sign in again).
Does nothing on other systems or when `osascript` isn't available."""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
import threading

log = logging.getLogger("notify")


def mac_notify(title: str, text: str) -> None:
    if sys.platform != "darwin" or not shutil.which("osascript"):
        return
    quote = lambda s: '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    try:
        subprocess.run(["osascript", "-e", f"display notification {quote(text)} with title {quote(title)} sound name \"Glass\""],
                       timeout=5, check=False, capture_output=True)
    except Exception as e:  # a missed banner must never stop the app
        log.info("notification failed", extra={"outcome": str(e)[:100]})


class Notifier:
    """Watches the database every `every_s` seconds and sends a banner when something new needs you."""

    def __init__(self, db, controls, every_s: float = 20.0, send=mac_notify):
        self.db, self.controls, self.every_s, self.send = db, controls, every_s, send
        self._stop = threading.Event()
        self._last = {"waiting": 0, "stuck": 0, "paused": False}

    def check(self) -> None:
        waiting = self.db.scalar("SELECT COUNT(*) FROM jobs WHERE state IN ('awaiting_approval', 'needs_review') "
                                 "AND kind IN ('intro', 'reply')")
        stuck = self.db.scalar("SELECT COUNT(*) FROM outbox WHERE state = 'unknown'")
        paused = self.controls.run_state.value != "running" or self.controls.emergency_stopped
        if waiting > self._last["waiting"]:
            self.send("Conversation Agent", f"{waiting} message{'s' if waiting != 1 else ''} waiting for your OK")
        if stuck > self._last["stuck"]:
            self.send("Conversation Agent", "Not sure if a message went through, have a look")
        if paused and not self._last["paused"]:
            self.send("Conversation Agent", f"Paused: {self.controls.pause_reason or 'open the dashboard'}")
        self._last = {"waiting": waiting, "stuck": stuck, "paused": paused}

    def start(self) -> "Notifier":
        def loop():
            while not self._stop.wait(self.every_s):
                try:
                    self.check()
                except Exception as e:
                    log.info("notifier check failed", extra={"outcome": str(e)[:100]})
        threading.Thread(target=loop, name="notifier", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()
