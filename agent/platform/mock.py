"""Synthetic social platform with deterministic fault injection.

MockWorld holds the platform's ground truth. MockPlatform is a direct adapter over it; the mock web site
(agent.platform.mock_site) renders the same world as HTML so the browser adapter can be tested for real.
"""
from __future__ import annotations

import itertools
import threading
import time
from dataclasses import dataclass, field

from ..core.db import iso
from ..core.errors import (AmbiguousState, AuthenticationRequired, BrowserCrashed, DeliveryUnknown, PermissionRestricted,
                           PlatformUnavailable, RateLimited, SelectorDrift)
from .base import Capabilities, FollowerObservation, ObservedMessage, SendResult, ThreadSnapshot


@dataclass
class Faults:
    auth_expired: bool = False
    outage: bool = False
    rate_limited: bool = False
    selector_drift_for: set[str] = field(default_factory=set)     # usernames whose DM can't be opened
    selector_drift_all: bool = False                               # UI changed everywhere
    no_message_button: set[str] = field(default_factory=set)
    ambiguous: set[str] = field(default_factory=set)               # usernames with two matching threads
    duplicate_follower_events: bool = False
    send_mode: str = "normal"   # normal | fail | unknown_delivered | unknown_not_delivered | crash_after_delivery
    send_mode_once: bool = True
    hang_s: float = 0.0          # every call blocks this long (stall simulation)
    reads_fail_once: set[str] = field(default_factory=set)


@dataclass
class MockAccount:
    username: str
    display_name: str
    platform_id: str
    bio: str = ""


class MockWorld:
    def __init__(self, me: str = "lukehorbach2"):
        self.me = me
        self.accounts: dict[str, MockAccount] = {}
        self.follower_events: list[dict] = []
        self.threads: dict[str, dict] = {}
        self.faults = Faults()
        self._ids = itertools.count(1)
        self.lock = threading.RLock()
        self.sent_log: list[tuple[str, str]] = []   # ground truth of every delivered outbound message
        self.unfollowed: set[str] = set()           # accounts the operator had the agent unfollow

    def next_id(self, prefix: str) -> str:
        return f"{prefix}{next(self._ids)}"

    def add_account(self, username: str, display_name: str = "", bio: str = "") -> MockAccount:
        with self.lock:
            acct = MockAccount(username, display_name or username.title(), self.next_id("pid"), bio)
            self.accounts[username] = acct
            return acct

    def new_follower(self, username: str, display_name: str = "", bio: str = "") -> None:
        with self.lock:
            if username not in self.accounts:
                self.add_account(username, display_name, bio)
            self.follower_events.append({"id": self.next_id("fev"), "username": username, "at": iso()})

    def thread_for(self, username: str, create: bool = True) -> str | None:
        with self.lock:
            for key, t in self.threads.items():
                if t["username"] == username:
                    return key
            if not create:
                return None
            key = self.next_id("t")
            self.threads[key] = {"username": username, "messages": []}
            return key

    def incoming(self, username: str, text: str) -> str:
        with self.lock:
            key = self.thread_for(username)
            mid = self.next_id("m")
            self.threads[key]["messages"].append({"id": mid, "dir": "in", "text": text, "at": iso()})
            return mid

    def deliver(self, thread_key: str, text: str) -> str:
        with self.lock:
            mid = self.next_id("m")
            self.threads[thread_key]["messages"].append({"id": mid, "dir": "out", "text": text, "at": iso()})
            self.sent_log.append((self.threads[thread_key]["username"], text))
            return mid


class MockPlatform:
    is_real = False

    def __init__(self, world: MockWorld):
        self.w = world
        self._cursor = 0
        self.calls = 0

    def _gate(self) -> None:
        self.calls += 1
        f = self.w.faults
        if f.hang_s:
            time.sleep(f.hang_s)
        if f.auth_expired:
            raise AuthenticationRequired("mock session expired; login page shown")
        if f.outage:
            raise PlatformUnavailable("mock platform returned 503")
        if f.rate_limited:
            raise RateLimited("mock platform: try again later", retry_after_s=600)

    def capabilities(self) -> Capabilities:
        return Capabilities(follower_events="verified", read_threads=True, send=True,
                            delivery_check="authoritative", message_ids="verified")

    def whoami(self) -> str:
        self._gate()
        return self.w.me

    def health(self) -> dict:
        return {"ok": not (self.w.faults.outage or self.w.faults.auth_expired), "adapter": "mock"}

    def poll_followers(self) -> list[FollowerObservation]:
        self._gate()
        with self.w.lock:
            events = self.w.follower_events[self._cursor:]
            if self.w.faults.duplicate_follower_events:
                events = events + events  # at-least-once delivery: same event twice
            self._cursor = len(self.w.follower_events)
            return [FollowerObservation(e["username"], self.w.accounts[e["username"]].display_name,
                                        self.w.accounts[e["username"]].platform_id, "verified", e["id"]) for e in events]

    def _snapshot(self, key: str) -> ThreadSnapshot:
        t = self.w.threads[key]
        return ThreadSnapshot(key, t["username"], "verified",
                              [ObservedMessage(m["id"], "verified", m["dir"], m["text"], m["at"]) for m in t["messages"]])

    def list_threads(self) -> list[ThreadSnapshot]:
        self._gate()
        with self.w.lock:
            return [self._snapshot(k) for k in self.w.threads]

    def read_thread(self, thread_key: str) -> ThreadSnapshot:
        self._gate()
        with self.w.lock:
            if thread_key not in self.w.threads:
                raise AmbiguousState(f"thread {thread_key} not found")
            user = self.w.threads[thread_key]["username"]
            if user in self.w.faults.reads_fail_once:
                self.w.faults.reads_fail_once.discard(user)
                raise BrowserCrashed("browser disconnected while reading the thread")
            return self._snapshot(thread_key)

    def open_dm(self, username: str) -> ThreadSnapshot:
        self._gate()
        f = self.w.faults
        if f.selector_drift_all or username in f.selector_drift_for:
            raise SelectorDrift(f"Message button for {username} not found")
        if username in f.no_message_button:
            raise PermissionRestricted(f"{username} can't be messaged")
        if username in f.ambiguous:
            raise AmbiguousState(f"two conversations match {username}")
        if username not in self.w.accounts:
            raise PermissionRestricted(f"{username} does not exist")
        return self._snapshot(self.w.thread_for(username))

    def send(self, thread_key: str, text: str, operation_id: str) -> SendResult:
        self._gate()
        f = self.w.faults
        mode = f.send_mode
        if f.send_mode_once:
            f.send_mode = "normal"
        if mode == "fail":
            return SendResult("failed", detail="mock: send rejected")
        if mode == "unknown_not_delivered":
            raise DeliveryUnknown("mock: send timed out before confirmation")
        mid = self.w.deliver(thread_key, text)
        if mode == "unknown_delivered":
            raise DeliveryUnknown("mock: delivered but confirmation timed out")
        if mode == "crash_after_delivery":
            raise BrowserCrashed("mock: browser crashed right after clicking send")
        return SendResult("sent", mid)

    def find_sent(self, thread_key: str, text: str, since_iso: str):
        self._gate()
        with self.w.lock:
            msgs = self.w.threads.get(thread_key, {}).get("messages", [])
            return "found" if any(m["dir"] == "out" and m["text"] == text and m["at"] >= since_iso for m in msgs) else "absent"

    def unfollow(self, username: str) -> str:
        self._gate()
        with self.w.lock:
            if username in self.w.unfollowed:
                return "not_following"
            self.w.unfollowed.add(username)
            return "unfollowed"

    def close(self) -> None:
        pass
