"""Browser supervisor: owns the Chrome lifecycle and turns every browser failure into a classified AgentError.

The browser is treated as an unreliable dependency. Every navigation uses explicit, bounded waits on observable
conditions (URL, ready element), revalidates where we ended up, and detects login walls and challenges.
Playwright's sync API is single-threaded, so all calls happen on the scheduler's worker thread.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Callable, TypeVar
from urllib.parse import urlsplit

from ..core.config import BrowserConfig
from ..core.errors import (AmbiguousState, AuthenticationRequired, BrowserCrashed, InterventionRequired, PlatformUnavailable,
                           SelectorDrift, TransientError)
from ..core.states import BROWSER_TRANSITIONS, BrowserState, check
from .sites import SiteProfile

log = logging.getLogger("browser")
T = TypeVar("T")

CRASH_MARKERS = ("has been closed", "Target closed", "browser has disconnected", "Browser closed", "Connection closed",
                 "Target page, context or browser", "Browser has been disconnected")
STALE_MARKERS = ("Execution context was destroyed", "frame was detached", "Frame was detached", "Cannot find context")


class BrowserSupervisor:
    def __init__(self, cfg: BrowserConfig, site: SiteProfile, launcher: Callable | None = None):
        self.cfg = cfg
        self.site = site
        self.state = BrowserState.STOPPED
        self._pw = None
        self.context = None
        self.page = None
        self.recoveries = 0
        self.restarts = 0
        self.last_error: str | None = None
        self._launcher = launcher
        self.window_request: str | None = None   # set from other threads; applied on the browser's own thread

    # ---------------------------------------------------------------- lifecycle
    def _to(self, target: BrowserState) -> None:
        if target is self.state:
            return
        check(BROWSER_TRANSITIONS, self.state, target)
        log.info("browser state", extra={"transition": f"{self.state.value}->{target.value}"})
        self.state = target

    def start(self) -> None:
        from playwright.sync_api import sync_playwright
        self._to(BrowserState.STARTING)
        try:
            if self._pw is None:
                self._pw = sync_playwright().start()
            if self._launcher:
                self.context = self._launcher(self._pw, self.cfg)
            else:
                self.cfg.user_data_dir.mkdir(parents=True, exist_ok=True)
                if self.cfg.headless:
                    kwargs = dict(headless=True, viewport={"width": 1400, "height": 900})
                else:
                    # A visible window uses the real window size, opened full screen, so what you see is the page.
                    # Keep pages running at full speed when the window is behind other apps or minimised.
                    kwargs = dict(headless=False, no_viewport=True,
                                  args=["--start-maximized", "--disable-backgrounding-occluded-windows",
                                        "--disable-renderer-backgrounding", "--disable-background-timer-throttling"])
                if self.cfg.executable_path:
                    kwargs["executable_path"] = self.cfg.executable_path
                self.context = self._pw.chromium.launch_persistent_context(str(self.cfg.user_data_dir), **kwargs)
            self.context.set_default_timeout(self.cfg.action_timeout_ms)
            self.context.set_default_navigation_timeout(self.cfg.navigation_timeout_ms)
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
            if not self.cfg.headless and not self._launcher:
                self.set_window("minimized" if self.cfg.start_minimized else "maximized")
            self.restarts += 1
            self._to(BrowserState.READY)
        except Exception as e:
            self._to(BrowserState.FAILED)
            raise PlatformUnavailable(f"browser failed to start: {e}") from e

    def set_window(self, state: str) -> bool:
        """Maximise or minimise the visible window ('maximized' | 'minimized' | 'normal'). --start-maximized is
        ignored on macOS, so this asks Chromium directly. Cosmetic: failures are ignored."""
        try:
            cdp = self.context.new_cdp_session(self.page)
            win = cdp.send("Browser.getWindowForTarget")["windowId"]
            if state != "normal":
                cdp.send("Browser.setWindowBounds", {"windowId": win, "bounds": {"windowState": "normal"}})
            cdp.send("Browser.setWindowBounds", {"windowId": win, "bounds": {"windowState": state}})
            cdp.detach()
            return True
        except Exception:
            return False

    def stop(self) -> None:
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        self.context = self.page = self._pw = None
        if self.state is not BrowserState.STOPPED:
            self.state = BrowserState.STOPPED

    def ensure_ready(self) -> None:
        if self.window_request and self.state is BrowserState.READY and not self.cfg.headless:
            self.set_window(self.window_request)
            self.window_request = None
        if self.state is BrowserState.AUTH_REQUIRED:
            # Re-check: the user may have logged in by hand in the visible browser.
            self._to(BrowserState.READY)
        if self.state in (BrowserState.STOPPED, BrowserState.FAILED):
            self.start()
            return
        if not self.healthy():
            self.recover("health check failed")

    def healthy(self) -> bool:
        if self.page is None or self.page.is_closed():
            return False
        try:
            self.page.wait_for_function("() => document.readyState !== undefined", timeout=self.cfg.health_check_timeout_ms)
            return True
        except Exception as e:
            self.last_error = str(e)[:300]
            return False

    def recover(self, reason: str) -> None:
        """Bounded restart. Exhausting the budget pauses the application rather than looping."""
        if self.recoveries >= self.cfg.max_recoveries_per_run:
            if self.state is not BrowserState.FAILED:
                self.state = BrowserState.FAILED
            raise InterventionRequired(f"browser recovery budget exhausted ({self.recoveries}); last problem: {reason}")
        self.recoveries += 1
        log.warning("recovering browser", extra={"decision": "restart", "outcome": reason})
        if self.state in (BrowserState.READY,):
            self._to(BrowserState.DEGRADED)
        if self.state in (BrowserState.DEGRADED, BrowserState.READY):
            self._to(BrowserState.RECOVERING)
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        self.context = self.page = None
        self.state = BrowserState.STOPPED
        self.start()

    def health(self) -> dict:
        info = {"state": self.state.value, "restarts": self.restarts, "recoveries": self.recoveries, "last_error": self.last_error}
        if self.page is not None and not self.page.is_closed():
            try:
                info["url"] = self.site.redact_url(self.page.url)
                info["js_heap_mb"] = self.page.evaluate("() => performance.memory ? Math.round(performance.memory.usedJSHeapSize/1048576) : null")
            except Exception as e:
                info["probe_error"] = str(e)[:200]
        return info

    # ---------------------------------------------------------------- guarded operations
    def run(self, what: str, fn: Callable[[], T]) -> T:
        """Run a browser action, translating raw Playwright failures into classified errors."""
        from playwright.sync_api import Error as PWError, TimeoutError as PWTimeout
        self.ensure_ready()
        try:
            return fn()
        except (AuthenticationRequired, SelectorDrift, AmbiguousState, PlatformUnavailable, TransientError) as e:
            raise
        except PWTimeout as e:
            raise SelectorDrift(f"{what}: timed out waiting for the page ({str(e).splitlines()[0][:200]})") from e
        except PWError as e:
            msg = str(e)
            if any(m in msg for m in CRASH_MARKERS):
                self.state = BrowserState.DEGRADED if self.state is BrowserState.READY else self.state
                self.page = None
                raise BrowserCrashed(f"{what}: browser closed or crashed") from e
            if any(m in msg for m in STALE_MARKERS):
                raise TransientError(f"{what}: page changed underneath the action (stale context)") from e
            if "net::ERR" in msg:
                raise PlatformUnavailable(f"{what}: network error {msg.split()[0]}") from e
            raise AmbiguousState(f"{what}: {msg.splitlines()[0][:200]}") from e

    def goto(self, url: str, *, expect: str, ready: str) -> None:
        """Navigate, then verify: not a login/challenge page, URL matches `expect` (regex), `ready` selector visible."""
        def nav():
            try:
                resp = self.page.goto(url, wait_until="domcontentloaded", timeout=self.cfg.navigation_timeout_ms)
            except Exception as e:
                from playwright.sync_api import TimeoutError as PWTimeout
                if isinstance(e, PWTimeout):
                    raise PlatformUnavailable(f"page did not load within {self.cfg.navigation_timeout_ms} ms: {self.site.redact_url(url)}") from e
                raise
            self.check_session()
            if resp is not None and resp.status >= 500:
                raise PlatformUnavailable(f"site returned {resp.status}")
            if resp is not None and resp.status == 429:
                from ..core.errors import RateLimited
                raise RateLimited("site returned 429 Too Many Requests", retry_after_s=900)
            if not re.search(expect, self.page.url):
                raise AmbiguousState(f"unexpected redirect to {self.site.redact_url(self.page.url)}")
            try:
                # Only visible matches count: sites like Instagram keep hidden copies of containers (e.g. empty
                # role=dialog elements) earlier in the page, and waiting on the first match would wait on a hidden one.
                self.page.locator(ready).filter(visible=True).first.wait_for(
                    state="visible", timeout=self.site.ready_timeout_ms or self.cfg.action_timeout_ms)
            except Exception as e:
                self.check_session()
                path = urlsplit(self.page.url).path
                self.save_screenshot("selector-drift-" + ("inbox" if path.startswith("/direct/inbox") else
                                                          "thread" if path.startswith("/direct/t/") else "page"))
                raise SelectorDrift(f"expected element not found on {self.site.redact_url(self.page.url)}: {ready}") from e
        self.run(f"goto {self.site.redact_url(url)}", nav)

    def save_screenshot(self, label: str) -> None:
        """Save a screenshot of what the page showed, for the operator. Local only (data/diagnostics is git-ignored);
        one file per label, overwritten each time. Never raises."""
        try:
            out = self.cfg.user_data_dir.parent / "diagnostics"
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"last-{label}.png"
            self.page.screenshot(path=str(path), timeout=self.cfg.health_check_timeout_ms)
            log.info("saved screenshot", extra={"outcome": str(path)})
        except Exception:
            pass

    def check_session(self) -> None:
        """Detect login walls and verification challenges. Never interacts with them."""
        url = self.page.url
        if any(re.search(p, url) for p in self.site.login_url_patterns):
            self.state = BrowserState.AUTH_REQUIRED
            raise AuthenticationRequired("login required: please sign in in the agent's browser window, then resume")
        if any(re.search(p, url) for p in self.site.challenge_url_patterns):
            self.state = BrowserState.AUTH_REQUIRED
            raise AuthenticationRequired("the site is asking for verification; complete it yourself in the browser, then resume")
        for sel in self.site.login_selectors:
            try:
                if self.page.locator(sel).filter(visible=True).count() > 0:
                    self.state = BrowserState.AUTH_REQUIRED
                    raise AuthenticationRequired("a login form is shown: please sign in, then resume")
            except AuthenticationRequired:
                raise
            except Exception:
                continue
