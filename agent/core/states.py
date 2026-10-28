"""Explicit state machines. Illegal transitions raise; every transition is persisted with a reason."""
from __future__ import annotations

from enum import StrEnum

from .errors import DataIntegrityError


class JobState(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    INSPECTING = "inspecting"
    REASONING = "reasoning"
    AWAITING_APPROVAL = "awaiting_approval"
    READY_TO_SEND = "ready_to_send"
    SENDING = "sending"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    NEEDS_REVIEW = "needs_review"
    RETRY_SCHEDULED = "retry_scheduled"
    FAILED = "failed"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    DELIVERY_UNKNOWN = "delivery_unknown"


J = JobState
TERMINAL_JOB_STATES = {J.COMPLETED, J.SKIPPED, J.FAILED, J.CANCELLED}
# States a worker holds a lease in. If the process dies here, nothing external has happened yet
# (side effects only start in SENDING), so restart can safely requeue them.
PRE_SIDE_EFFECT_STATES = {J.CLAIMED, J.INSPECTING, J.REASONING}

JOB_TRANSITIONS: dict[JobState, set[JobState]] = {
    J.QUEUED: {J.CLAIMED, J.CANCELLED, J.SKIPPED, J.BLOCKED},
    J.CLAIMED: {J.INSPECTING, J.QUEUED, J.RETRY_SCHEDULED, J.SKIPPED, J.NEEDS_REVIEW, J.FAILED, J.CANCELLED, J.BLOCKED},
    J.INSPECTING: {J.REASONING, J.READY_TO_SEND, J.QUEUED, J.RETRY_SCHEDULED, J.SKIPPED, J.NEEDS_REVIEW, J.FAILED, J.CANCELLED, J.BLOCKED, J.COMPLETED},
    J.REASONING: {J.AWAITING_APPROVAL, J.READY_TO_SEND, J.QUEUED, J.RETRY_SCHEDULED, J.SKIPPED, J.NEEDS_REVIEW, J.FAILED, J.CANCELLED, J.BLOCKED, J.COMPLETED},
    J.AWAITING_APPROVAL: {J.READY_TO_SEND, J.SKIPPED, J.CANCELLED, J.REASONING},
    # READY_TO_SEND -> CLAIMED/INSPECTING lets a worker re-verify identity before sending after approval.
    # -> QUEUED / RETRY_SCHEDULED are safe here: nothing has been attempted and jobs resume from the outbox row.
    J.READY_TO_SEND: {J.CLAIMED, J.SENDING, J.SKIPPED, J.CANCELLED, J.BLOCKED, J.NEEDS_REVIEW, J.QUEUED, J.RETRY_SCHEDULED},
    J.SENDING: {J.VERIFYING, J.DELIVERY_UNKNOWN, J.RETRY_SCHEDULED, J.FAILED, J.NEEDS_REVIEW},
    J.VERIFYING: {J.COMPLETED, J.DELIVERY_UNKNOWN, J.NEEDS_REVIEW, J.FAILED},
    # -> RETRY_SCHEDULED only after an authoritative check confirmed the message was NOT delivered.
    J.DELIVERY_UNKNOWN: {J.COMPLETED, J.NEEDS_REVIEW, J.FAILED, J.RETRY_SCHEDULED},
    J.RETRY_SCHEDULED: {J.QUEUED, J.CANCELLED, J.SKIPPED},
    J.BLOCKED: {J.QUEUED, J.CANCELLED, J.SKIPPED},
    J.NEEDS_REVIEW: {J.QUEUED, J.SKIPPED, J.CANCELLED, J.COMPLETED, J.FAILED},
    J.COMPLETED: set(), J.SKIPPED: set(), J.FAILED: set(), J.CANCELLED: set(),
}


class OutboxState(StrEnum):
    PENDING = "pending"          # persisted, not attempted
    ATTEMPTING = "attempting"    # an attempt started; outcome not yet recorded
    SENT = "sent"                # confirmed delivered
    FAILED = "failed"            # confirmed not delivered
    UNKNOWN = "unknown"          # attempted, outcome can't be established
    CANCELLED = "cancelled"      # cancelled before any attempt


O = OutboxState
OUTBOX_TRANSITIONS: dict[OutboxState, set[OutboxState]] = {
    O.PENDING: {O.ATTEMPTING, O.CANCELLED},
    O.ATTEMPTING: {O.SENT, O.FAILED, O.UNKNOWN},
    O.UNKNOWN: {O.SENT, O.FAILED},          # only via reconciliation or an explicit operator decision
    O.FAILED: {O.PENDING},                  # a confirmed failure may be re-queued (no duplicate risk)
    O.SENT: set(), O.CANCELLED: set(),
}


class BrowserState(StrEnum):
    STOPPED = "stopped"
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"        # health check failing, recovery pending
    RECOVERING = "recovering"
    AUTH_REQUIRED = "auth_required"
    FAILED = "failed"            # recovery budget exhausted


B = BrowserState
BROWSER_TRANSITIONS: dict[BrowserState, set[BrowserState]] = {
    B.STOPPED: {B.STARTING},
    B.STARTING: {B.READY, B.AUTH_REQUIRED, B.FAILED, B.STOPPED},
    B.READY: {B.DEGRADED, B.AUTH_REQUIRED, B.STOPPED, B.RECOVERING},
    B.DEGRADED: {B.RECOVERING, B.READY, B.STOPPED, B.FAILED},
    B.RECOVERING: {B.READY, B.AUTH_REQUIRED, B.FAILED, B.STOPPED},
    B.AUTH_REQUIRED: {B.READY, B.STOPPED, B.STARTING},
    B.FAILED: {B.STOPPED, B.STARTING},
}


class ConversationStatus(StrEnum):
    ACTIVE = "active"
    OPTED_OUT = "opted_out"
    NEEDS_REVIEW = "needs_review"


def check(table: dict, current, target) -> None:
    if target not in table.get(current, set()):
        raise DataIntegrityError(f"illegal transition {current} -> {target}")
