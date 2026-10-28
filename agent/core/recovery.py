"""Error classification, backoff, circuit breaker, application controls and the recovery decision policy.

The decision table is deterministic: given the error category and the counters on the job, account and
platform, it always produces the same decision. See docs/RECOVERY.md.
"""
from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from .config import CircuitConfig, LimitsConfig
from .db import Database, iso, now, parse
from .errors import (AgentError, AmbiguousState, AuthenticationRequired, BrowserCrashed, DataIntegrityError,
                     DeliveryUnknown, ErrorCategory, ModelFailure, OperatorStop, PermissionRestricted,
                     PlatformUnavailable, RateLimited, SelectorDrift, TransientError)

C = ErrorCategory


def classify(exc: BaseException) -> ErrorCategory:
    """Map an exception to a category. Unknown exceptions are PERMANENT, not transient: retrying an
    error we don't understand is how duplicate side effects happen."""
    if isinstance(exc, AgentError):
        return exc.category
    if isinstance(exc, sqlite3.IntegrityError):
        return C.DATA_INTEGRITY            # constraint violated: our state disagrees with itself
    if isinstance(exc, sqlite3.OperationalError):
        return C.TRANSIENT                 # database unavailable (locked, busy, I/O): the transaction rolled back
    if isinstance(exc, sqlite3.DatabaseError):
        return C.DATA_INTEGRITY            # malformed / corrupt file
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return C.TRANSIENT
    return C.PERMANENT


def backoff_delay(attempt: int, limits: LimitsConfig, rng: random.Random | None = None) -> float:
    """Exponential backoff with full jitter, bounded by backoff_max_s."""
    rng = rng or random.Random()
    cap = min(limits.backoff_max_s, limits.backoff_base_s * (2 ** max(0, attempt - 1)))
    return rng.uniform(cap / 2, cap)


class RunState(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"


class Controls:
    """Operator and system controls, persisted so they survive restarts."""

    def __init__(self, db: Database):
        self.db = db

    @property
    def run_state(self) -> RunState:
        return RunState(self.db.get_state("run_state", RunState.RUNNING.value))

    @property
    def pause_reason(self) -> str | None:
        return self.db.get_state("pause_reason")

    @property
    def emergency_stopped(self) -> bool:
        return bool(self.db.get_state("emergency_stop", False))

    def pause(self, reason: str, actor: str = "system") -> None:
        with self.db.transaction():
            if self.run_state is RunState.STOPPED:
                return
            self.db.set_state("run_state", RunState.PAUSED.value)
            self.db.set_state("pause_reason", reason)
            self.db.audit(actor, "pause", reason)

    def resume(self, actor: str = "operator") -> None:
        with self.db.transaction():
            if self.emergency_stopped:
                raise OperatorStop("emergency stop is active; clear it explicitly first")
            self.db.set_state("run_state", RunState.RUNNING.value)
            self.db.set_state("pause_reason", None)
            self.db.audit(actor, "resume")

    def stop(self, reason: str, actor: str = "system") -> None:
        with self.db.transaction():
            self.db.set_state("run_state", RunState.STOPPED.value)
            self.db.set_state("pause_reason", reason)
            self.db.audit(actor, "stop", reason)

    def emergency_stop(self, actor: str = "operator") -> None:
        with self.db.transaction():
            self.db.set_state("emergency_stop", True)
            self.db.set_state("run_state", RunState.STOPPED.value)
            self.db.set_state("pause_reason", "emergency stop")
            self.db.audit(actor, "emergency_stop")

    def clear_emergency_stop(self, actor: str = "operator") -> None:
        with self.db.transaction():
            self.db.set_state("emergency_stop", False)
            self.db.set_state("run_state", RunState.PAUSED.value)
            self.db.set_state("pause_reason", "emergency stop cleared; resume when ready")
            self.db.audit(actor, "clear_emergency_stop")

    def guard(self, action: str) -> None:
        """Called before every consequential action. Raises if the action must not proceed."""
        if self.emergency_stopped:
            raise OperatorStop(f"emergency stop active; refused: {action}")
        if self.run_state is not RunState.RUNNING:
            raise OperatorStop(f"application {self.run_state.value} ({self.pause_reason}); refused: {action}")


class CircuitBreaker:
    """Opens when `failure_threshold` distinct subjects fail with the same platform-level category within the window."""

    PLATFORM_CATEGORIES = {C.SELECTOR_DRIFT, C.PLATFORM_UNAVAILABLE, C.RATE_LIMITED, C.TRANSIENT, C.AMBIGUOUS_STATE}

    def __init__(self, db: Database, cfg: CircuitConfig):
        self.db = db
        self.cfg = cfg

    def _load(self) -> dict:
        return self.db.get_state("circuit", {"state": "closed", "opened_at": None, "reason": None, "failures": []})

    @property
    def state(self) -> str:
        c = self._load()
        if c["state"] == "open" and (now() - parse(c["opened_at"])).total_seconds() >= self.cfg.cooldown_s:
            return "half_open"
        return c["state"]

    def info(self) -> dict:
        c = self._load()
        c["effective_state"] = self.state
        return c

    def allow(self) -> bool:
        return self.state != "open"

    def record_failure(self, subject: str, category: ErrorCategory) -> bool:
        """Returns True if this failure opened the circuit."""
        if category not in self.PLATFORM_CATEGORIES:
            return False
        with self.db.transaction():
            c = self._load()
            cutoff = now() - timedelta(seconds=self.cfg.window_s)
            failures = [f for f in c["failures"] if parse(f["at"]) >= cutoff]
            failures.append({"subject": subject, "category": category.value, "at": iso()})
            c["failures"] = failures[-200:]
            same = {f["subject"] for f in failures if f["category"] == category.value}
            opened = False
            if c["state"] != "open" and len(same) >= self.cfg.failure_threshold:
                c.update(state="open", opened_at=iso(), reason=f"{len(same)} accounts failed with {category.value}")
                opened = True
            elif c["state"] == "open" and self.state == "half_open":
                # A failure during the half-open probe re-opens for another cooldown.
                c.update(opened_at=iso(), reason=f"probe failed: {category.value}")
            self.db.set_state("circuit", c)
            return opened

    def record_success(self) -> None:
        with self.db.transaction():
            c = self._load()
            if c["state"] != "closed":
                self.db.audit("system", "circuit_closed", c.get("reason"))
            self.db.set_state("circuit", {"state": "closed", "opened_at": None, "reason": None, "failures": []})

    def force_open(self, reason: str) -> None:
        with self.db.transaction():
            c = self._load()
            c.update(state="open", opened_at=iso(), reason=reason)
            self.db.set_state("circuit", c)


class Action(StrEnum):
    RETRY = "retry"                       # Case A
    SKIP_ACCOUNT = "skip_account"         # Case B
    PAUSE_APP = "pause_app"               # Case C (auth / browser budget exhausted)
    OPEN_CIRCUIT = "open_circuit"         # Case D
    QUARANTINE_STOP = "quarantine_stop"   # Case E
    RECONCILE = "reconcile"               # Case F
    STOP_APP = "stop_app"                 # operator stop


@dataclass
class Decision:
    action: Action
    category: ErrorCategory
    reason: str
    delay_s: float = 0.0


class RecoveryManager:
    def __init__(self, db: Database, limits: LimitsConfig, circuit: CircuitBreaker, controls: Controls,
                 rng: random.Random | None = None):
        self.db = db
        self.limits = limits
        self.circuit = circuit
        self.controls = controls
        self.rng = rng or random.Random()

    def decide(self, *, exc: BaseException, subject: str, attempts: int, category_count: int,
               account_failures: int) -> Decision:
        cat = classify(exc)
        msg = str(exc) or type(exc).__name__
        if isinstance(exc, OperatorStop):
            return Decision(Action.STOP_APP, cat, msg)
        if cat is C.DATA_INTEGRITY:
            return Decision(Action.QUARANTINE_STOP, cat, f"data integrity: {msg}")
        if cat is C.DELIVERY_UNKNOWN:
            return Decision(Action.RECONCILE, cat, msg)
        if cat is C.AUTHENTICATION:
            return Decision(Action.PAUSE_APP, cat, f"login or verification needed: {msg}")
        if cat is C.PERMISSION:
            return Decision(Action.SKIP_ACCOUNT, cat, f"not permitted for this account: {msg}")
        if cat is C.PERMANENT:
            return Decision(Action.SKIP_ACCOUNT, cat, msg)
        if cat is C.OPERATOR_INTERVENTION_REQUIRED:
            return Decision(Action.PAUSE_APP, cat, msg)
        # Platform-level categories feed the breaker.
        if self.circuit.record_failure(subject, cat):
            return Decision(Action.OPEN_CIRCUIT, cat, f"circuit opened: {msg}")
        if cat is C.RATE_LIMITED:
            # Respect the platform: don't retry this job soon, pause everything that depends on it.
            return Decision(Action.OPEN_CIRCUIT, cat, f"rate limited: {msg}")
        if cat in (C.SELECTOR_DRIFT, C.AMBIGUOUS_STATE) and account_failures + 1 >= self.limits.consecutive_account_failures:
            return Decision(Action.SKIP_ACCOUNT, cat, f"repeated {cat.value} on this account: {msg}")
        if cat in (C.TRANSIENT, C.PLATFORM_UNAVAILABLE, C.MODEL_FAILURE, C.SELECTOR_DRIFT, C.AMBIGUOUS_STATE):
            if attempts >= self.limits.max_attempts or category_count > self.limits.max_retries_per_category:
                return Decision(Action.SKIP_ACCOUNT, cat, f"retry budget exhausted ({attempts} attempts): {msg}")
            retry_after = getattr(exc, "retry_after_s", None)
            delay = max(retry_after or 0.0, backoff_delay(attempts, self.limits, self.rng))
            return Decision(Action.RETRY, cat, msg, delay)
        return Decision(Action.SKIP_ACCOUNT, cat, msg)

    def record(self, decision: Decision, *, scope: str, job_id: str | None, subject: str | None) -> None:
        self.db.execute(
            "INSERT INTO recovery_events(at, scope, job_id, subject, category, decision, detail) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (iso(), scope, job_id, subject, decision.category.value, decision.action.value,
             json.dumps({"reason": decision.reason[:500], "delay_s": round(decision.delay_s, 2)})))


__all__ = ["classify", "backoff_delay", "Controls", "RunState", "CircuitBreaker", "RecoveryManager", "Decision", "Action",
           "AuthenticationRequired", "BrowserCrashed", "DataIntegrityError", "DeliveryUnknown", "ModelFailure",
           "PermissionRestricted", "PlatformUnavailable", "RateLimited", "SelectorDrift", "AmbiguousState", "TransientError"]
