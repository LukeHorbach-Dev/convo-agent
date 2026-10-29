"""Platform adapter interface. Business logic depends only on this, never on Playwright or a specific site."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol

Provenance = Literal["verified", "inferred"]


@dataclass(frozen=True)
class Capabilities:
    follower_events: Literal["verified", "inferred", "unavailable"]
    read_threads: bool
    send: bool
    delivery_check: Literal["authoritative", "best_effort", "unavailable"]
    message_ids: Provenance
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class FollowerObservation:
    username: str
    display_name: str | None
    platform_id: str | None
    source: Provenance          # verified: the platform told us; inferred: we diffed a list
    event_id: str | None        # stable event id if the platform provides one


@dataclass(frozen=True)
class ObservedMessage:
    platform_message_id: str
    id_source: Provenance       # inferred ids are content hashes and can collide for identical messages
    direction: Literal["in", "out"]
    text: str
    sent_at: str | None = None


@dataclass
class ThreadSnapshot:
    thread_key: str
    username: str
    identity_source: Provenance
    messages: list[ObservedMessage] = field(default_factory=list)


@dataclass(frozen=True)
class SendResult:
    status: Literal["sent", "failed", "unknown"]
    platform_message_id: str | None = None
    detail: str = ""


class PlatformAdapter(Protocol):
    is_real: bool

    def capabilities(self) -> Capabilities: ...
    def whoami(self) -> str: ...
    def health(self) -> dict: ...
    def poll_followers(self) -> list[FollowerObservation]: ...
    def list_threads(self) -> list[ThreadSnapshot]: ...
    def read_thread(self, thread_key: str) -> ThreadSnapshot: ...
    def open_dm(self, username: str) -> ThreadSnapshot: ...
    def send(self, thread_key: str, text: str, operation_id: str) -> SendResult: ...
    def find_sent(self, thread_key: str, text: str, since_iso: str) -> Literal["found", "absent", "unknown"]: ...
    def unfollow(self, username: str) -> Literal["unfollowed", "not_following"]: ...
    def close(self) -> None: ...
