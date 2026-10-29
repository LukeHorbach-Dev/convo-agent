"""Browser-backed platform adapter. Site-specific details come from a SiteProfile; failures come back classified."""
from __future__ import annotations

import logging
import re
import time
from collections import Counter
from urllib.parse import urlsplit

from ..core.errors import AmbiguousState, AuthenticationRequired, DeliveryUnknown, PermissionRestricted, SelectorDrift
from .base import Capabilities, FollowerObservation, ObservedMessage, SendResult, ThreadSnapshot
from .browser import BrowserSupervisor
from .sites import SiteProfile, content_id

USERNAME = re.compile(r"^/([A-Za-z0-9._]{1,30})/?$")
log = logging.getLogger("web")
RESERVED = {"direct", "explore", "reels", "accounts", "stories", "p", "reel", "about", "legal", "challenge", "api"}


def username_from_href(href: str | None) -> str | None:
    if not href:
        return None
    m = USERNAME.match(re.sub(r"^https?://[^/]+", "", href))
    return m.group(1) if m and m.group(1).lower() not in RESERVED else None


class WebPlatform:
    def __init__(self, sup: BrowserSupervisor, site: SiteProfile, me: str, max_threads: int = 20, max_scrolls: int = 8,
                 audience: str = "followers"):
        self.sup = sup
        self.site = site
        self.me = me
        self.is_real = site.is_real
        self.max_threads = max_threads
        self.max_scrolls = max_scrolls
        self.audience = audience                 # which list on your profile: "followers" or "following"
        self.batch_wait_s = 8.0                  # how long to wait for the next batch of the list after scrolling
        self.full_reread_s = 600.0
        self._rows_read: dict[str, None] = {}   # inbox row texts already read (insertion-ordered, capped)
        self._last_full_read = time.monotonic()

    def capabilities(self) -> Capabilities:
        return Capabilities(follower_events=self.site.follower_events, read_threads=True, send=True,
                            delivery_check=self.site.delivery_check, message_ids=self.site.message_ids, notes=self.site.notes)

    def health(self) -> dict:
        return {"ok": self.sup.state.value == "ready", "adapter": self.site.name, **self.sup.health()}

    @property
    def page(self):
        return self.sup.page

    def _pause(self) -> None:
        """A short, steady pause after a click so the next page or pop-up can finish loading."""
        if self.site.settle_ms:
            self.page.wait_for_timeout(self.site.settle_ms // 2)

    def _visible(self, selector: str):
        """Locator restricted to visible elements; hidden duplicates (common on Instagram) are ignored."""
        return self.page.locator(selector).filter(visible=True)

    def whoami(self) -> str:
        if (marker := self.site.own_profile_marker()) is not None:
            self.sup.goto(self.site.url(self.site.profile_path(self.me)), expect=re.escape(f"/{self.me}/"), ready="main")
            def check():
                if self._visible(marker).count() == 0:
                    self.sup.save_screenshot("identity")
                    raise AuthenticationRequired(f"{self.me}'s profile has no Edit profile button: the browser seems to be "
                                                 f"signed in to a different account (or the button moved)")
                return self.me
            return self.sup.run("whoami", check)
        self.sup.goto(self.site.url(self.site.inbox_path()), expect=r"/direct/", ready=self.site.nav_profile())
        def read():
            user = username_from_href(self._visible(self.site.nav_profile()).first.get_attribute("href"))
            if not user:
                raise AmbiguousState("could not read the logged-in account from the navigation")
            return user
        return self.sup.run("whoami", read)

    def poll_followers(self) -> list[FollowerObservation]:
        """Read the list chosen by `audience` (your followers, or the people you follow), newest first.
        Opened the way a person does: your profile, then the list link. Loading the list's address directly
        doesn't open the pop-up on Instagram in every browser session."""
        which = self.audience
        link = self.site.list_link(self.me, which)
        self.sup.goto(self.site.url(self.site.profile_path(self.me)), expect=re.escape(f"/{self.me}/"), ready=link)
        def open_list():
            self._visible(link).first.click()
            self._pause()
            try:
                self._visible(self.site.followers_ready()).first.wait_for(
                    state="visible", timeout=self.site.ready_timeout_ms or self.sup.cfg.action_timeout_ms)
            except Exception as e:
                self.sup.save_screenshot(f"selector-drift-{which}")
                raise SelectorDrift(f"{which} list did not open after clicking {link}: {self.site.followers_ready()}") from e
        log.info(f"opening {which} list", extra={"outcome": f"clicking the {which} count on your profile"})
        self.sup.run(f"open {which}", open_list)
        def right_list():
            # Seen live: the app opened Followers when it meant Following. Never read a list without its title.
            other = "followers" if which == "following" else "following"
            try:
                self._visible(self.site.list_title(which)).first.wait_for(state="visible", timeout=self.sup.cfg.action_timeout_ms)
                wrong = self._visible(self.site.list_title(other)).count() and which == "following"
            except Exception:
                wrong = True
            if wrong:
                self.sup.save_screenshot(f"selector-drift-{which}")
                self.page.keyboard.press("Escape")
                raise SelectorDrift(f"the list that opened isn't your {which} list")
        self.sup.run(f"check {which} list", right_list)
        def read():
            links = self._visible(self.site.follower_links())
            try:
                # The pop-up appears first and fills in a moment later.
                links.first.wait_for(state="visible", timeout=self.site.ready_timeout_ms or self.sup.cfg.action_timeout_ms)
            except Exception as e:
                self.sup.save_screenshot(f"selector-drift-{which}")
                raise SelectorDrift(f"{which} list opened but no accounts appeared in it: {self.site.follower_links()}") from e
            seen: dict[str, str | None] = {}
            for _ in range(self.max_scrolls):
                # One round trip per batch: a list of 1,000+ accounts makes per-link calls very slow.
                for href, name in links.evaluate_all("els => els.map(e => [e.getAttribute('href'), e.dataset.displayName || null])"):
                    u = username_from_href(href)
                    if u and u != self.me and u not in seen:
                        seen[u] = name
                shown = links.count()
                links.last.scroll_into_view_if_needed()
                links.last.hover()
                self.page.mouse.wheel(0, 2500)       # a real scroll inside the list, which is what loads more
                if self.site.settle_ms:
                    self.page.wait_for_timeout(self.site.settle_ms)
                links.last.evaluate("""a => { for (let e = a.parentElement; e; e = e.parentElement)
                    if (e.scrollHeight > e.clientHeight + 10 && getComputedStyle(e).overflowY !== 'visible') {
                      e.scrollTop = e.scrollHeight; break; } }""")
                for _ in range(int(self.batch_wait_s * 4)):   # give the next batch time to load
                    if links.count() > shown:
                        break
                    self.page.wait_for_timeout(250)
                else:
                    break                            # nothing more loaded: end of the list
            return [FollowerObservation(u, d, None, "inferred", None) for u, d in seen.items()]
        found = self.sup.run(f"read {which}", read)
        log.info(f"read {which} list", extra={"outcome": f"{len(found)} accounts seen"})
        return found

    ROW_TEXT_JS = """el => { const c = el.cloneNode(true); c.querySelectorAll('abbr').forEach(a => a.remove());
                             return c.innerText.replace(/\\s*·\\s*$/, '').replace(/\\s+/g, ' ').trim(); }"""

    def _open_inbox(self) -> None:
        self.sup.goto(self.site.url(self.site.inbox_path()), expect=r"/direct/", ready=self.site.inbox_ready())

    def list_threads(self) -> list[ThreadSnapshot]:
        """Inbox rows have no links, so each conversation is opened by clicking its row. Only rows whose text
        (name and latest-message preview, minus the time) changed since they were last read are opened; everything
        is re-read every `full_reread_s` in case a new message repeats the previous preview exactly."""
        if time.monotonic() - self._last_full_read > self.full_reread_s:
            self._rows_read.clear()
            self._last_full_read = time.monotonic()
        self._open_inbox()
        rows = self._visible(self.site.thread_rows())
        texts = self.sup.run("list threads", lambda: [r.evaluate(self.ROW_TEXT_JS) for r in rows.all()[: self.max_threads]])
        snaps: list[ThreadSnapshot] = []
        for text in texts:
            if text in self._rows_read:
                continue
            if snaps:
                self._open_inbox()
            def open_row(text=text):
                for r in rows.all()[: self.max_threads]:
                    if r.evaluate(self.ROW_TEXT_JS) == text:
                        r.click()
                        self._pause()
                        self.page.wait_for_url(re.compile(r"/direct/t/[^/]+"), timeout=self.sup.cfg.action_timeout_ms)
                        self._visible(self.site.thread_ready()).first.wait_for(
                            state="visible", timeout=self.site.ready_timeout_ms or self.sup.cfg.action_timeout_ms)
                        return re.search(r"/direct/t/([^/]+)", self.page.url).group(1)
                return None                          # the row changed or moved away meanwhile; next poll gets it
            key = self.sup.run("open thread", open_row)
            if key is None:
                continue
            snaps.append(self._read_open_thread(key))
            self._rows_read[text] = None
            while len(self._rows_read) > 500:
                self._rows_read.pop(next(iter(self._rows_read)))
        log.info("read inbox", extra={"outcome": f"{len(texts)} conversations, {len(snaps)} opened"})
        return snaps

    def _read_open_thread(self, key: str) -> ThreadSnapshot:
        def read():
            names = {username_from_href(a.get_attribute("href")) for a in self._visible(self.site.thread_username()).all()}
            names.discard(None)
            names.discard(self.me)
            if len(names) != 1:
                raise AmbiguousState(f"thread {key}: could not identify a single other participant ({len(names)} found)")
            raw = self.page.evaluate(self.site.read_messages_js())
            if raw is None:
                raise SelectorDrift("thread content area not found")
            seen: Counter = Counter()
            msgs = []
            for m in raw:
                if m["id"]:
                    msgs.append(ObservedMessage(m["id"], "verified", m["direction"], m["text"]))
                else:
                    seen[(m["direction"], m["text"])] += 1
                    msgs.append(ObservedMessage(content_id(m["direction"], m["text"], seen[(m["direction"], m["text"])]),
                                                "inferred", m["direction"], m["text"]))
            return ThreadSnapshot(key, names.pop(), "verified" if self.site.message_ids == "verified" else "inferred", msgs)
        return self.sup.run(f"read thread {key}", read)

    def read_thread(self, thread_key: str) -> ThreadSnapshot:
        self.sup.goto(self.site.url(self.site.thread_path(thread_key)), expect=re.escape(f"/direct/t/{thread_key}"),
                      ready=self.site.thread_ready())
        return self._read_open_thread(thread_key)

    def open_dm(self, username: str) -> ThreadSnapshot:
        self.sup.goto(self.site.url(self.site.profile_path(username)), expect=re.escape(f"/{username}"), ready="body")
        def click():
            # The profile header renders after the page loads: wait until either a Message control or a
            # "can't message" marker shows, instead of deciding on a half-built page.
            either = ", ".join([self.site.message_button(), *self.site.restricted_markers()])
            try:
                self._visible(either).first.wait_for(state="visible",
                                                     timeout=self.site.ready_timeout_ms or self.sup.cfg.action_timeout_ms)
            except Exception:
                self.sup.save_screenshot("selector-drift-profile")
            for marker in self.site.restricted_markers():
                if self._visible(marker).count():
                    raise PermissionRestricted(f"{username}: messaging is not available ({marker})")
            btn = self._visible(self.site.message_button())
            n = btn.count()
            if n == 0 and self._visible(self.site.follow_button()).count() and not self._visible(self.site.following_button()).count():
                raise PermissionRestricted(f"{username}: you don't follow them, so their profile has no Message button")
            if n == 0:
                raise SelectorDrift(f"{username}: no Message control found on the profile")
            btn.first.click()                         # the first one is the profile's own button
            self._pause()
            return self._wait_for_conversation(username)
        key = self.sup.run(f"open dm {username}", click)
        if key is None and (url := self.site.dm_url(username)):
            # Fallback: Instagram's own "message this account" address.
            log.info("opening conversation by address", extra={"outcome": "the chat pop-up could not be expanded"})
            self.sup.goto(url, expect=re.escape(urlsplit(self.site.base_url).netloc), ready="body")
            key = self.sup.run(f"open dm {username} by address", lambda: self._wait_for_conversation(username))
        if key is None:
            self.sup.save_screenshot("selector-drift-dm")
            raise AmbiguousState(f"{username}: Message opened, but not as a full conversation")
        return self._read_open_thread(key)

    def _wait_for_conversation(self, username: str) -> str | None:
        """After pressing Message: Instagram opens either the full conversation (/direct/t/<id>/) or a small chat
        pop-up over the profile, which has no address to come back to. Expand the pop-up if there is one. Returns
        the conversation id, or None if no full conversation opened in time."""
        deadline = time.monotonic() + (self.site.ready_timeout_ms or self.sup.cfg.action_timeout_ms) / 1000
        expanded = False
        while not re.search(r"/direct/t/[^/]+", self.page.url):
            if not expanded and self._visible(self.site.dm_expand()).count():
                self._visible(self.site.dm_expand()).first.click()
                self._pause()
                expanded = True
            if time.monotonic() > deadline:
                log.info("conversation did not open", extra={"outcome": "expanded pop-up" if expanded else "no expand control"})
                return None
            self.page.wait_for_timeout(250)
        self._visible(self.site.composer()).first.wait_for(
            state="visible", timeout=self.site.ready_timeout_ms or self.sup.cfg.action_timeout_ms)
        return re.search(r"/direct/t/([^/]+)/?", self.page.url).group(1)

    def unfollow(self, username: str) -> str:
        """Unfollow from their profile: Following -> Unfollow (and Unfollow again if Instagram asks), then check the
        button now says Follow. Safe to repeat: if it already says Follow, nothing is clicked."""
        self.sup.goto(self.site.url(self.site.profile_path(username)), expect=re.escape(f"/{username}"), ready="body")
        timeout = self.site.ready_timeout_ms or self.sup.cfg.action_timeout_ms
        def act():
            # Wait for the profile's own controls; Follow buttons can also belong to suggested accounts.
            either = ", ".join([self.site.following_button(), self.site.message_button(), *self.site.restricted_markers()])
            try:
                self._visible(either).first.wait_for(state="visible", timeout=timeout)
            except Exception:
                self.sup.save_screenshot("selector-drift-unfollow")
                raise SelectorDrift(f"{username}: no Following/Follow button on the profile")
            if self._visible(self.site.following_button()).count() == 0:
                if self._visible(self.site.follow_button()).count():
                    return "not_following"
                raise PermissionRestricted(f"{username}: profile isn't available")
            self._visible(self.site.following_button()).first.click()
            self._pause()
            confirm = self._visible(self.site.unfollow_confirm())
            try:
                confirm.first.wait_for(state="visible", timeout=self.sup.cfg.action_timeout_ms)
            except Exception:
                self.page.keyboard.press("Escape")
                self.sup.save_screenshot("selector-drift-unfollow")
                raise SelectorDrift(f"{username}: the Following menu has no Unfollow option")
            confirm.first.click()
            self._pause()
            try:                                     # private accounts: "Unfollow?" asks once more
                confirm.first.wait_for(state="visible", timeout=1500)
                confirm.first.click()
            except Exception:
                pass
            # Done when the profile no longer says Following (Follow buttons elsewhere on the page, like
            # suggested accounts, prove nothing).
            deadline = time.monotonic() + timeout / 1000
            while self._visible(self.site.following_button()).count():
                if time.monotonic() > deadline:
                    self.sup.save_screenshot("selector-drift-unfollow")
                    raise AmbiguousState(f"{username}: pressed Unfollow, but the profile still says Following")
                self.page.wait_for_timeout(250)
            return "unfollowed"
        return self.sup.run(f"unfollow {username}", act)

    def send(self, thread_key: str, text: str, operation_id: str) -> SendResult:
        snap = self.read_thread(thread_key)
        before = sum(1 for m in snap.messages if m.direction == "out" and m.text == text)

        def type_and_send():
            box = self._visible(self.site.composer()).first
            box.fill(text)
            typed = box.input_value() if box.evaluate("e => 'value' in e") else box.inner_text()
            if typed.strip() != text.strip():
                raise SelectorDrift("the message box did not accept the text")
            # Instagram only shows Send once there is text; if it never appears, Enter sends from the message box.
            send = self._visible(self.site.send_button())
            try:
                send.first.wait_for(state="visible", timeout=2000)
                send.first.click()
            except Exception:
                if send.count():
                    raise
                box.press("Enter")
        # Up to the click, failure means nothing was sent. After it, failure means "unknown".
        self.sup.run("type message", lambda: self._visible(self.site.composer()).first.wait_for(state="visible"))
        try:
            self.sup.run("send", type_and_send)
        except SelectorDrift:
            raise
        except Exception as e:
            raise DeliveryUnknown(f"send interrupted: {e}") from e

        def confirmed() -> bool:
            msgs = self.page.evaluate(self.site.read_messages_js()) or []
            return sum(1 for m in msgs if m["direction"] == "out" and m["text"] == text) > before
        # Explicit bounded wait on an observable condition: our message appearing in the thread.
        deadline = time.monotonic() + self.sup.cfg.action_timeout_ms / 1000
        try:
            while not self.sup.run("confirm send", confirmed):
                if time.monotonic() > deadline:
                    raise DeliveryUnknown("message did not appear in the thread before the timeout")
                self.page.wait_for_timeout(250)
        except DeliveryUnknown:
            raise
        except Exception as e:
            raise DeliveryUnknown(f"could not confirm the send: {e}") from e
        after = self._read_open_thread(thread_key)
        mine = [m for m in after.messages if m.direction == "out" and m.text == text]
        return SendResult("sent", mine[-1].platform_message_id if mine else None)

    def find_sent(self, thread_key: str, text: str, since_iso: str):
        snap = self.read_thread(thread_key)
        # The page shows no reliable timestamps, so this can only say whether the text is present at all.
        return "found" if any(m.direction == "out" and m.text == text for m in snap.messages) else "absent"

    def close(self) -> None:
        self.sup.stop()
