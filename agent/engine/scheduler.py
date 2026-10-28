"""Scheduler: one worker thread for platform work (the browser is single-session), plus an independent watchdog
thread so a hung worker is still detected. A file lock prevents two scheduler instances on one database."""
from __future__ import annotations

import fcntl
import logging
import os
import threading
from pathlib import Path

from ..core.recovery import classify
from .engine import Engine

log = logging.getLogger("scheduler")


class InstanceLock:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = open(self.path, "w")
        try:
            fcntl.flock(self._fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._fh.close()
            raise RuntimeError(f"another scheduler is already running on this database ({self.path})")
        self._fh.write(str(os.getpid()))
        self._fh.flush()

    def release(self) -> None:
        if self._fh:
            fcntl.flock(self._fh, fcntl.LOCK_UN)
            self._fh.close()
            self._fh = None


class Scheduler:
    def __init__(self, engine: Engine, poll_interval_s: float, watchdog_interval_s: float = 15.0):
        self.engine = engine
        self.poll = poll_interval_s
        self.watchdog_interval = watchdog_interval_s
        self._stop = threading.Event()
        self._wake = threading.Event()
        self.worker: threading.Thread | None = None
        self.watchdog: threading.Thread | None = None
        self.last_tick: dict = {}
        self.last_error: str | None = None
        self.heartbeat_at: float | None = None
        self.platform_health: dict | None = None

    def start(self) -> None:
        self.engine.startup_recovery()
        self.worker = threading.Thread(target=self._work, name="worker", daemon=True)
        self.watchdog = threading.Thread(target=self._watch, name="watchdog", daemon=True)
        self.worker.start()
        self.watchdog.start()

    def wake(self) -> None:
        self._wake.set()

    def _work(self) -> None:
        import time
        while not self._stop.is_set():
            try:
                self.heartbeat_at = time.time()
                self.last_tick = self.engine.run_once()
                self.last_error = None
            except Exception as e:  # never let the loop die; failures are classified and visible
                self.last_error = f"{classify(e).value}: {e}"
                log.exception("scheduler tick failed")
            try:
                # Health is probed on the worker thread (Playwright is single-threaded) and cached for the dashboard.
                self.platform_health = self.engine.platform.health()
            except Exception as e:
                self.platform_health = {"ok": False, "error": str(e)[:200]}
            self._wake.wait(self.poll)
            self._wake.clear()

    def _watch(self) -> None:
        while not self._stop.wait(self.watchdog_interval):
            try:
                self.engine.watchdog()
            except Exception:
                log.exception("watchdog tick failed")

    def stop(self, timeout_s: float = 30.0) -> bool:
        """Graceful: no new ticks start; the current job finishes its step (every step is transactional)."""
        self._stop.set()
        self._wake.set()
        ok = True
        for t in (self.worker, self.watchdog):
            if t:
                t.join(timeout_s)
                ok = ok and not t.is_alive()
        return ok
