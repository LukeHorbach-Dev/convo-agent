"""Centralized messaging policy. Decisions here are pure functions so they can be tested exhaustively."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .config import Mode, Settings
from .errors import PolicyViolation

# A birth year of 2007 or later in a username suggests the person may be under 19. Conservative on purpose.
_YOUNG_YEAR = re.compile(r"(?:^|\D)20(?:0[7-9]|1\d|2[0-6])(?!\d)")


@dataclass(frozen=True)
class SendPermission:
    allowed: bool
    requires_approval: bool
    reason: str


def requires_approval(settings: Settings, platform_is_real: bool) -> bool:
    # Real platforms always require a human approval per message. This is deliberately not configurable.
    if platform_is_real:
        return True
    return settings.policy.mock_requires_approval


def can_draft(settings: Settings) -> bool:
    return settings.mode in (Mode.DRAFT, Mode.SIMULATE, Mode.EXECUTE)


def can_send(settings: Settings, platform_is_real: bool) -> SendPermission:
    if settings.mode in (Mode.OBSERVE, Mode.DRAFT):
        return SendPermission(False, True, f"mode {settings.mode.value} never sends")
    if settings.mode is Mode.SIMULATE and platform_is_real:
        return SendPermission(False, True, "simulate mode cannot send on a real platform")
    return SendPermission(True, requires_approval(settings, platform_is_real), "ok")


def assert_deliverable(*, platform_is_real: bool, approved_by: str | None) -> None:
    """Last gate before any external send. On a real platform the outbox row must carry an operator approval."""
    if platform_is_real and approved_by != "operator":
        raise PolicyViolation("real-platform message has no operator approval; refusing to send")


def load_never_list(settings: Settings) -> None:
    path = settings.policy.never_list_path
    if path and Path(path).exists():
        names = [l.strip().lstrip("@") for l in Path(path).read_text().splitlines() if l.strip() and not l.startswith("#")]
        settings.policy.excluded_usernames = sorted({*settings.policy.excluded_usernames, *names})


def is_excluded(settings: Settings, username: str) -> bool:
    return username.lower() in {x.lower() for x in settings.policy.excluded_usernames}


def intro_eligibility(settings: Settings, *, username: str, intro_status: str, opted_out: bool,
                      review_status: str) -> tuple[bool, str]:
    u = username.lower()
    if u in {x.lower() for x in settings.policy.excluded_usernames}:
        return False, "on the never-message list"
    if _YOUNG_YEAR.search(u):
        return False, "username suggests they may be under the minimum age"
    if opted_out:
        return False, "asked not to be contacted"
    if intro_status in ("sent", "pending"):
        return False, f"introduction already {intro_status}"
    if intro_status == "unknown":
        # Ambiguous contact status: never send another intro just to resolve it.
        return False, "earlier introduction has an unknown delivery outcome"
    if review_status != "ok":
        return False, f"account is {review_status}"
    return True, "eligible"


DISENGAGE = re.compile(
    r"\b(stop (messag|text|talk)\w*|leave me alone|don'?t (message|text|contact|talk to) me|not interested|"
    r"unsubscribe|go away|block(ing)? you|please stop|no thanks?,? (stop|bye))\b", re.I)


def wants_to_disengage(text: str) -> bool:
    return bool(DISENGAGE.search(text))


AGE_STATEMENT = re.compile(
    r"\b(?:i'?m|i am|im)\s+(\d{1,3})(?:\s*(?:yo|y/o|years?(?: old)?|yrs?))?\b(?!\s*(?:min|mins|minutes|hours?|hrs|km|kg|%|am|pm))"
    r"|\b(\d{1,3})\s*(?:yo|y/o|yrs? old|years? old)\b", re.I)


def stated_age(text: str) -> int | None:
    m = AGE_STATEMENT.search(text)
    if not m:
        return None
    return int(m.group(1) or m.group(2))


def age_blocks_contact(settings: Settings, text: str) -> bool:
    age = stated_age(text)
    return age is not None and (age < settings.policy.min_age or age > 90)
