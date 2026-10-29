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
    def own_profile_marker(self) -> str | None: return None   # shown only on your own profile; None = read the nav
    def profile_path(self, username: str) -> str: return f"/{username}/"
    def followers_path(self, me: str) -> str: return f"/{me}/followers/"
    def list_link(self, me: str, which: str = "followers") -> str:   # on your profile; opens that list
        return f'a[href$="/{me}/{which}/"]'
    def follower_links(self) -> str: raise NotImplementedError
    def followers_ready(self) -> str: raise NotImplementedError
    def inbox_ready(self) -> str: raise NotImplementedError
    def inbox_path(self) -> str: return "/direct/inbox/"
    def thread_rows(self) -> str: raise NotImplementedError    # inbox entries; clicking one opens /direct/t/<id>/
    def thread_path(self, key: str) -> str: return f"/direct/t/{key}/"
    def thread_ready(self) -> str: raise NotImplementedError
    def thread_username(self) -> str: raise NotImplementedError
    def message_button(self) -> str: raise NotImplementedError
    def composer(self) -> str: raise NotImplementedError
    def send_button(self) -> str: raise NotImplementedError
    def restricted_markers(self) -> list[str]: return []
    def dm_expand(self) -> str: return '[aria-label="Expand"]'   # on a chat pop-up: opens the full conversation
    def dm_url(self, username: str) -> str | None: return None    # an address that opens a conversation, if any
    # On someone's profile: the button showing you follow them, the one showing you don't, and the menu item that
    # unfollows (Instagram: "Following" opens a menu; private accounts ask again in a second dialog).
    def following_button(self) -> str:
        # Exact text: the "228 following" count in the same header is also a button on Instagram.
        return 'main header [role="button"]:text-is("Following"), main header button:text-is("Following")'
    def follow_button(self) -> str:
        return ", ".join(f'main header {t}:text-is("{x}")' for t in ('[role="button"]', "button") for x in ("Follow", "Follow Back"))
    def unfollow_confirm(self) -> str:
        return ('[role="dialog"] [role="button"]:text-is("Unfollow"), [role="dialog"] button:text-is("Unfollow"), '
                '[role="menuitem"]:text-is("Unfollow")')
    def read_messages_js(self) -> str: raise NotImplementedError


class MockSiteProfile(SiteProfile):
    def nav_profile(self): return '[data-testid="nav-profile"]'
    def follower_links(self): return 'div[role="dialog"] [data-testid="follower"]'
    def followers_ready(self): return 'div[role="dialog"]'
    def inbox_ready(self): return '[data-testid="inbox-ready"]'
    def thread_rows(self): return '[aria-label="Thread list"] [data-testid="thread-row"]'
    def thread_ready(self): return '[data-testid="thread-username"]'
    def thread_username(self): return '[data-testid="thread-username"]'
    def message_button(self): return '[data-testid="message-button"]'
    def composer(self): return '[data-testid="composer"]'
    def send_button(self): return '[data-testid="send-button"]'
    def restricted_markers(self): return ['[data-testid="messaging-restricted"]', ':text("Sorry, this page isn\'t available")']
    def dm_url(self, username): return self.url(f"/m/{username}/")

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

    def own_profile_marker(self):
        # The sidebar collapses to icons on some pages, so identity is checked on the profile page instead: only
        # the signed-in account sees "Edit profile" there.
        return 'header a[href="/accounts/edit/"], header a:has-text("Edit profile")'

    def follower_links(self):
        return 'div[role="dialog"] a[href^="/"]'

    def list_link(self, me: str, which: str = "followers") -> str:
        # The "796 followers" / "1,465 following" link in your profile header; the text fallback covers a header
        # without that href.
        return f'a[href$="/{me}/{which}/"], header a:has-text("{which}")'

    def thread_rows(self):
        # Inbox rows are div[role=button] without links, inside div[aria-label="Thread list"]. Conversation rows show a
        # time (<abbr>); the notes strip above them doesn't. Innermost such button only.
        return 'div[aria-label="Thread list"] div[role="button"]:has(abbr):not(:has(div[role="button"] abbr))'

    def followers_ready(self):
        return 'div[role="dialog"]'

    def inbox_ready(self):
        return 'div[aria-label="Thread list"]'

    def thread_ready(self):
        return '[role="main"] [role="textbox"][contenteditable="true"], [role="main"] [role="grid"]'

    def thread_username(self):
        return '[role="main"] header a[href^="/"], [role="main"] a[href^="/"][role="link"]'

    def message_button(self):
        # Seen live: a div[role=button] reading "Message" in the profile header. Kept to <main> so the sidebar's
        # "Messages" link never matches (text-is is exact).
        return ('main header [role="button"]:text-is("Message"), main header button:text-is("Message"), '
                'main [role="button"]:text-is("Message")')

    def composer(self):
        return '[role="textbox"][contenteditable="true"]'

    def send_button(self):
        return '[role="button"]:text-is("Send"), [aria-label="Send"]'

    def dm_url(self, username):
        # Instagram's own short link for "message this account"; used only if the pop-up can't be expanded.
        return f"https://ig.me/m/{username}"

    def dm_expand(self):
        # Seen live: Message on a profile can open a small chat pop-up (bottom right) with an expand icon top right.
        return '[aria-label*="xpand" i], [aria-label*="full screen" i], [aria-label*="Open in" i]'

    def restricted_markers(self):
        return [':text("Sorry, this page isn\'t available")', ':text("User not found")', 'main header :text-is("Requested")', 'main :text("This account is private")']

    def read_messages_js(self) -> str:
        # No stable ids or roles for bubbles: read innermost dir=auto text in the conversation pane. The pane is the
        # column above the message box (the inbox list sits to its left inside the same main element); bubbles right
        # of the pane's centre are ours. Centred lines (dates, "Seen", "You're now friends") are status, not messages.
        return """() => {
          const main = document.querySelector('[role="main"]'); if (!main) return null;
          const box = main.querySelector('[contenteditable="true"]');
          const r = main.getBoundingClientRect(), pane = box ? box.getBoundingClientRect() : r;
          const left = box ? pane.left - 60 : r.left, right = box ? pane.right + 60 : r.right, mid = (left + right) / 2;
          const status = /^(seen|sent|delivered|seen by .+|active (now|.+ ago)|you're now friends.*|say hi!?|((today|yesterday|mon|tue|wed|thu|fri|sat|sun)\\w* )?\\d{1,2}:\\d{2}( ?[ap]m)?)$/i;
          const out = [];
          main.querySelectorAll('div[dir="auto"], span[dir="auto"]').forEach(e => {
            if ((box && box.contains(e)) || e.querySelector('[dir="auto"]') || e.closest('header, a, [role="button"]')) return;
            const t = e.innerText.trim(), b = e.getBoundingClientRect(), c = b.left + b.width / 2;
            if (!t || !b.width || b.top < r.top + 70 || c < left || c > right || status.test(t)) return;
            if (Math.abs(c - mid) < 30 && b.width < (right - left) * 0.5) return;
            out.push({id: null, direction: c > mid ? 'out' : 'in', text: t, top: b.top});
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
