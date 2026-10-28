"""Site profiles: everything site-specific about locating things on a page.

MockSiteProfile targets the synthetic site with stable data-testid attributes and is exercised by the browser
tests. InstagramProfile targets instagram.com with semantic/role-based locators. Instagram's markup is
obfuscated and changes often and it exposes no stable message ids, so that profile is best effort, marks its
observations as inferred, and is NOT covered by automated tests (it cannot be tested from CI).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit


def content_id(direction: str, text: str, occurrence: int) -> str:
    """Inferred message id for sites without stable ids. Identical messages are told apart by occurrence order."""
    return "h:" + hashlib.sha256(f"{direction}\x00{text}\x00{occurrence}".encode()).hexdigest()[:20]


@dataclass
class SiteProfile:
    name: str
    base_url: str
    is_real: bool
    follower_events: str                 # verified | inferred | unavailable
    delivery_check: str                  # authoritative | best_effort | unavailable
    message_ids: str                     # verified | inferred
    login_url_patterns: list[str] = field(default_factory=list)
    challenge_url_patterns: list[str] = field(default_factory=list)
    login_selectors: list[str] = field(default_factory=list)
    ready_timeout_ms: int | None = None  # how long to wait for a page's key element; None = the action timeout
    notes: tuple[str, ...] = ()

    def url(self, path: str) -> str:
        return self.base_url.rstrip("/") + path

    def redact_url(self, url: str) -> str:
        p = urlsplit(url)
        return f"{p.scheme}://{p.netloc}{p.path}"

    # --- locators (overridden per site) ---
    def nav_profile(self) -> str: raise NotImplementedError
    def profile_path(self, username: str) -> str: return f"/{username}/"
    def followers_path(self, me: str) -> str: return f"/{me}/followers/"
    def follower_links(self) -> str: raise NotImplementedError
    def followers_ready(self) -> str: raise NotImplementedError
    def inbox_ready(self) -> str: raise NotImplementedError
    def inbox_path(self) -> str: return "/direct/inbox/"
    def thread_links(self) -> str: raise NotImplementedError
    def thread_path(self, key: str) -> str: return f"/direct/t/{key}/"
    def thread_ready(self) -> str: raise NotImplementedError
    def thread_username(self) -> str: raise NotImplementedError
    def message_button(self) -> str: raise NotImplementedError
    def composer(self) -> str: raise NotImplementedError
    def send_button(self) -> str: raise NotImplementedError
    def restricted_markers(self) -> list[str]: return []
    def read_messages_js(self) -> str: raise NotImplementedError


class MockSiteProfile(SiteProfile):
    def nav_profile(self): return '[data-testid="nav-profile"]'
    def follower_links(self): return 'div[role="dialog"] [data-testid="follower"]'
    def followers_ready(self): return 'div[role="dialog"]'
    def inbox_ready(self): return '[data-testid="inbox-ready"]'
    def thread_links(self): return '[data-testid="thread-link"]'
    def thread_ready(self): return '[data-testid="thread-username"]'
    def thread_username(self): return '[data-testid="thread-username"]'
    def message_button(self): return '[data-testid="message-button"]'
    def composer(self): return '[data-testid="composer"]'
    def send_button(self): return '[data-testid="send-button"]'
    def restricted_markers(self): return ['[data-testid="messaging-restricted"]']

    def read_messages_js(self) -> str:
        return """() => [...document.querySelectorAll('[data-testid="message"]')].map(e => ({
            id: e.dataset.messageId || null, direction: e.dataset.direction, text: e.innerText.trim()}))"""


def mock_site_profile(base_url: str) -> MockSiteProfile:
    return MockSiteProfile(
        name="mock_web", base_url=base_url, is_real=False, follower_events="inferred", delivery_check="best_effort",
        message_ids="verified", login_url_patterns=[r"/accounts/login"], challenge_url_patterns=[r"/challenge/"],
        login_selectors=['[data-testid="login-form"]'],
        notes=("follower events are inferred by diffing the followers list",))


class InstagramProfile(SiteProfile):
    """Best-effort locators for instagram.com. Untested by the automated suite; see docs/LIMITATIONS.md."""

    def nav_profile(self):
        return 'a[role="link"]:has-text("Profile")'

    def follower_links(self):
        return 'div[role="dialog"] a[href^="/"]'

    def thread_links(self):
        return 'a[href^="/direct/t/"]'

    def followers_ready(self):
        return 'div[role="dialog"]'

    def inbox_ready(self):
        return 'div[role="main"], a[href^="/direct/t/"]'

    def thread_ready(self):
        return 'div[role="main"] [role="textbox"][contenteditable="true"], div[role="main"] [role="grid"]'

    def thread_username(self):
        return 'div[role="main"] header a[href^="/"], div[role="main"] a[href^="/"][role="link"]'

    def message_button(self):
        return 'main header [role="button"]:text-is("Message"), main header button:text-is("Message")'

    def composer(self):
        return '[role="textbox"][contenteditable="true"]'

    def send_button(self):
        return '[role="button"]:text-is("Send"), [aria-label="Send"]'

    def restricted_markers(self):
        return ['main header :text-is("Requested")', 'main :text("This account is private")']

    def read_messages_js(self) -> str:
        # No stable ids or roles for bubbles: read innermost dir=auto text in the thread; bubbles right of centre are ours.
        return """() => {
          const main = document.querySelector('div[role="main"]'); if (!main) return null;
          const r = main.getBoundingClientRect(), mid = r.left + r.width / 2, box = main.querySelector('[contenteditable="true"]');
          const out = [];
          main.querySelectorAll('div[dir="auto"], span[dir="auto"]').forEach(e => {
            if ((box && box.contains(e)) || e.querySelector('[dir="auto"]') || e.closest('header, a, [role="button"]')) return;
            const t = e.innerText.trim(), b = e.getBoundingClientRect();
            if (!t || !b.width || b.top < r.top + 70) return;
            out.push({id: null, direction: (b.left + b.width / 2) > mid ? 'out' : 'in', text: t, top: b.top});
          });
          return out.sort((a, b) => a.top - b.top);
        }"""


INSTAGRAM = InstagramProfile(
    name="instagram", base_url="https://www.instagram.com", is_real=True, follower_events="inferred",
    delivery_check="best_effort", message_ids="inferred",
    login_url_patterns=[r"/accounts/login"],
    challenge_url_patterns=[r"/challenge/", r"/checkpoint/", r"/accounts/suspended", r"/two_factor"],
    login_selectors=['input[name="password"]'],
    ready_timeout_ms=25_000,             # a full page load renders the app first and pop-ups like followers later
    notes=("Instagram Web has no new-follower event stream; followers are inferred by diffing the list",
           "message ids are content hashes (no stable ids in the page)",
           "delivery checks re-read the thread and are best effort",
           "locators are unverified against the live site"))
