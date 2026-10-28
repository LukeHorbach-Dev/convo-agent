"""Browser-backed platform adapter. Site-specific details come from a SiteProfile; failures come back classified."""
from __future__ import annotations

import logging
import re
from collections import Counter

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
    def __init__(self, sup: BrowserSupervisor, site: SiteProfile, me: str, max_threads: int = 20, max_scrolls: int = 8):
        self.sup = sup
        self.site = site
        self.me = me
        self.is_real = site.is_real
        self.max_threads = max_threads
        self.max_scrolls = max_scrolls

    def capabilities(self) -> Capabilities:
        return Capabilities(follower_events=self.site.follower_events, read_threads=True, send=True,
                            delivery_check=self.site.delivery_check, message_ids=self.site.message_ids, notes=self.site.notes)

    def health(self) -> dict:
        return {"ok": self.sup.state.value == "ready", "adapter": self.site.name, **self.sup.health()}

    @property
    def page(self):
        return self.sup.page

    def _visible(self, selector: str):
        """Locator restricted to visible elements; hidden duplicates (common on Instagram) are ignored."""
        return self.page.locator(selector).filter(visible=True)

    def whoami(self) -> str:
        if (marker := self.site.own_profile_marker()) is not None:
            self.sup.goto(self.site.url(self.site.profile_path(self.me)), expect=re.escape(f"/{self.me}/"), ready="header")
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
        # Open the list the way a person does: your profile, then the followers link. Loading /followers/ directly
        # doesn't open the pop-up on Instagram in every browser session.
        link = self.site.followers_link(self.me)
        self.sup.goto(self.site.url(self.site.profile_path(self.me)), expect=re.escape(f"/{self.me}/"), ready=link)
        def open_list():
            self._visible(link).first.click()
            try:
                self._visible(self.site.followers_ready()).first.wait_for(
                    state="visible", timeout=self.site.ready_timeout_ms or self.sup.cfg.action_timeout_ms)
            except Exception as e:
                self.sup.save_screenshot("selector-drift")
                raise SelectorDrift(f"followers list did not open after clicking {link}: {self.site.followers_ready()}") from e
        log.info("opening followers list", extra={"outcome": "clicking the followers link on your profile"})
        self.sup.run("open followers", open_list)
        def read():
            seen: dict[str, str | None] = {}
            for _ in range(self.max_scrolls):
                before = len(seen)
                for a in self._visible(self.site.follower_links()).all():
                    u = username_from_href(a.get_attribute("href"))
                    if u and u != self.me and u not in seen:
                        seen[u] = (a.get_attribute("data-display-name") or None)
                if len(seen) == before:
                    break
                self._visible(self.site.follower_links()).last.scroll_into_view_if_needed()
            return [FollowerObservation(u, d, None, "inferred", None) for u, d in seen.items()]
        found = self.sup.run("read followers", read)
        log.info("read followers list", extra={"outcome": f"{len(found)} followers seen"})
        return found

    def _thread_keys(self) -> list[str]:
        self.sup.goto(self.site.url(self.site.inbox_path()), expect=r"/direct/", ready=self.site.inbox_ready())
        def read():
            keys = []
            for a in self._visible(self.site.thread_links()).all()[: self.max_threads]:
                m = re.search(r"/direct/t/([^/]+)/?", a.get_attribute("href") or "")
                if m and m.group(1) not in keys:
                    keys.append(m.group(1))
            return keys
        return self.sup.run("list threads", read)

    def list_threads(self) -> list[ThreadSnapshot]:
        return [self.read_thread(k) for k in self._thread_keys()]

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
            for marker in self.site.restricted_markers():
                if self._visible(marker).count():
                    raise PermissionRestricted(f"{username}: messaging is not available ({marker})")
            btn = self._visible(self.site.message_button())
            n = btn.count()
            if n == 0:
                raise SelectorDrift(f"{username}: no Message control found on the profile")
            if n > 1:
                raise AmbiguousState(f"{username}: {n} Message controls found")
            btn.first.click()
            self._visible(self.site.composer()).first.wait_for(state="visible", timeout=self.sup.cfg.action_timeout_ms)
            m = re.search(r"/direct/t/([^/]+)/?", self.page.url)
            if not m:
                raise AmbiguousState(f"{username}: Message opened, but the conversation could not be identified")
            return m.group(1)
        key = self.sup.run(f"open dm {username}", click)
        return self._read_open_thread(key)

    def send(self, thread_key: str, text: str, operation_id: str) -> SendResult:
        snap = self.read_thread(thread_key)
        before = sum(1 for m in snap.messages if m.direction == "out" and m.text == text)

        def type_and_send():
            box = self._visible(self.site.composer()).first
            box.fill(text)
            typed = box.input_value() if box.evaluate("e => 'value' in e") else box.inner_text()
            if typed.strip() != text.strip():
                raise SelectorDrift("the message box did not accept the text")
            send = self._visible(self.site.send_button())
            if send.count() == 0:
                raise SelectorDrift("send control not found")
            send.first.click()
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
        import time
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
