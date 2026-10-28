"""Unit tests: state machines, classification, backoff, circuit breaker, controls, policy, job queue."""
from __future__ import annotations

import random
import sqlite3
import threading
from datetime import timedelta

import pytest

from agent.core import policy
from agent.core.config import LimitsConfig, Mode, PlatformKind, Settings
from agent.core.db import Database, iso, now
from agent.core.errors import (AuthenticationRequired, DataIntegrityError, DeliveryUnknown, ErrorCategory, ModelFailure,
                               OperatorStop, PermissionRestricted, PlatformUnavailable, PolicyViolation, QueueFull,
                               RateLimited, SelectorDrift, TransientError)
from agent.core.jobs import JobQueue
from agent.core.recovery import Action, CircuitBreaker, Controls, RecoveryManager, RunState, backoff_delay, classify
from agent.core.states import JOB_TRANSITIONS, OUTBOX_TRANSITIONS, TERMINAL_JOB_STATES, JobState, OutboxState, check

from .conftest import make_settings


# --------------------------------------------------------------------- state machines
def test_terminal_states_have_no_exits():
    for s in TERMINAL_JOB_STATES:
        assert JOB_TRANSITIONS[s] == set()


def test_illegal_job_transition_raises():
    with pytest.raises(DataIntegrityError):
        check(JOB_TRANSITIONS, JobState.QUEUED, JobState.SENDING)
    with pytest.raises(DataIntegrityError):
        check(JOB_TRANSITIONS, JobState.DELIVERY_UNKNOWN, JobState.QUEUED)   # never straight back to a resendable state


def test_outbox_unknown_cannot_go_back_to_pending():
    assert OutboxState.PENDING not in OUTBOX_TRANSITIONS[OutboxState.UNKNOWN]
    assert OUTBOX_TRANSITIONS[OutboxState.SENT] == set()


def test_every_state_reachable_from_queued():
    seen, todo = {JobState.QUEUED}, [JobState.QUEUED]
    while todo:
        for t in JOB_TRANSITIONS[todo.pop()]:
            if t not in seen:
                seen.add(t)
                todo.append(t)
    assert seen == set(JobState)


# --------------------------------------------------------------------- classification and backoff
@pytest.mark.parametrize("exc,cat", [
    (TransientError("x"), ErrorCategory.TRANSIENT), (AuthenticationRequired("x"), ErrorCategory.AUTHENTICATION),
    (PermissionRestricted("x"), ErrorCategory.PERMISSION), (RateLimited("x"), ErrorCategory.RATE_LIMITED),
    (SelectorDrift("x"), ErrorCategory.SELECTOR_DRIFT), (DeliveryUnknown("x"), ErrorCategory.DELIVERY_UNKNOWN),
    (ModelFailure("x"), ErrorCategory.MODEL_FAILURE), (ValueError("x"), ErrorCategory.PERMANENT),
    (sqlite3.OperationalError("database is locked"), ErrorCategory.TRANSIENT),
    (sqlite3.OperationalError("disk I/O error"), ErrorCategory.TRANSIENT),
    (sqlite3.IntegrityError("UNIQUE constraint failed"), ErrorCategory.DATA_INTEGRITY),
    (sqlite3.DatabaseError("malformed"), ErrorCategory.DATA_INTEGRITY), (TimeoutError(), ErrorCategory.TRANSIENT),
])
def test_classify(exc, cat):
    assert classify(exc) is cat


def test_unknown_exceptions_are_not_transient():
    assert classify(RuntimeError("weird")) is ErrorCategory.PERMANENT


def test_backoff_bounded_with_jitter():
    lim = LimitsConfig(backoff_base_s=2, backoff_max_s=60)
    rng = random.Random(1)
    delays = [backoff_delay(a, lim, rng) for a in range(1, 30)]
    assert all(0 < d <= 60 for d in delays)
    assert delays[0] <= 2 and max(delays) > 30
    assert len({round(d, 3) for d in delays}) > 5  # jittered


# --------------------------------------------------------------------- controls and circuit breaker
def test_emergency_stop_blocks_and_requires_explicit_clear():
    db = Database(":memory:"); db.migrate()
    c = Controls(db)
    c.guard("send")
    c.emergency_stop()
    with pytest.raises(OperatorStop):
        c.guard("send")
    with pytest.raises(OperatorStop):
        c.resume()
    c.pause("system wants to pause")             # pause can't override a stop
    assert c.run_state is RunState.STOPPED
    c.clear_emergency_stop()
    assert c.run_state is RunState.PAUSED
    c.resume()
    c.guard("send")


def test_circuit_opens_on_distinct_accounts_and_half_opens_after_cooldown():
    db = Database(":memory:"); db.migrate()
    s = make_settings()
    cb = CircuitBreaker(db, s.circuit)
    for i in range(s.circuit.failure_threshold - 1):
        assert not cb.record_failure(f"u{i}", ErrorCategory.SELECTOR_DRIFT)
    assert not cb.record_failure("u0", ErrorCategory.SELECTOR_DRIFT)        # same account again doesn't count twice
    assert cb.record_failure("last", ErrorCategory.SELECTOR_DRIFT)
    assert cb.state == "open" and not cb.allow()
    c = db.get_state("circuit")
    c["opened_at"] = iso(now() - timedelta(seconds=s.circuit.cooldown_s + 1))
    db.set_state("circuit", c)
    assert cb.state == "half_open" and cb.allow()
    cb.record_success()
    assert cb.state == "closed"


def test_permission_errors_do_not_feed_the_circuit():
    db = Database(":memory:"); db.migrate()
    cb = CircuitBreaker(db, make_settings().circuit)
    for i in range(20):
        assert not cb.record_failure(f"u{i}", ErrorCategory.PERMISSION)
    assert cb.state == "closed"


@pytest.mark.parametrize("exc,action", [
    (AuthenticationRequired("login"), Action.PAUSE_APP), (PermissionRestricted("no"), Action.SKIP_ACCOUNT),
    (DataIntegrityError("bad"), Action.QUARANTINE_STOP), (DeliveryUnknown("?"), Action.RECONCILE),
    (OperatorStop("stop"), Action.STOP_APP), (RateLimited("slow down"), Action.OPEN_CIRCUIT),
    (__import__("agent.core.errors", fromlist=["x"]).InterventionRequired("browser keeps dying"), Action.PAUSE_APP),
    (TransientError("blip"), Action.RETRY), (ModelFailure("timeout"), Action.RETRY), (ValueError("bug"), Action.SKIP_ACCOUNT),
])
def test_recovery_decision_table(exc, action):
    db = Database(":memory:"); db.migrate()
    s = make_settings()
    rm = RecoveryManager(db, s.limits, CircuitBreaker(db, s.circuit), Controls(db), random.Random(0))
    assert rm.decide(exc=exc, subject="a", attempts=1, category_count=1, account_failures=0).action is action


def test_retry_budget_exhausts_to_skip():
    db = Database(":memory:"); db.migrate()
    s = make_settings()
    rm = RecoveryManager(db, s.limits, CircuitBreaker(db, s.circuit), Controls(db))
    d = rm.decide(exc=TransientError("x"), subject="a", attempts=s.limits.max_attempts, category_count=1, account_failures=0)
    assert d.action is Action.SKIP_ACCOUNT


def test_repeated_selector_drift_on_one_account_skips_it():
    db = Database(":memory:"); db.migrate()
    s = make_settings()
    rm = RecoveryManager(db, s.limits, CircuitBreaker(db, s.circuit), Controls(db))
    d = rm.decide(exc=SelectorDrift("x"), subject="a", attempts=1, category_count=1, account_failures=s.limits.consecutive_account_failures)
    assert d.action is Action.SKIP_ACCOUNT


# --------------------------------------------------------------------- policy
def test_real_platform_always_requires_approval_regardless_of_config():
    s = make_settings()
    s.policy.mock_requires_approval = False
    for mode in (Mode.EXECUTE,):
        s.mode = mode
        perm = policy.can_send(s, platform_is_real=True)
        assert perm.allowed and perm.requires_approval
    assert not policy.can_send(make_settings(mode=Mode.SIMULATE), platform_is_real=True).allowed
    assert not policy.can_send(make_settings(mode=Mode.DRAFT), platform_is_real=False).allowed
    assert not policy.can_send(make_settings(mode=Mode.OBSERVE), platform_is_real=False).allowed


def test_deliverable_gate_requires_operator_on_real_platform():
    with pytest.raises(PolicyViolation):
        policy.assert_deliverable(platform_is_real=True, approved_by="policy:mock")
    with pytest.raises(PolicyViolation):
        policy.assert_deliverable(platform_is_real=True, approved_by=None)
    policy.assert_deliverable(platform_is_real=True, approved_by="operator")
    policy.assert_deliverable(platform_is_real=False, approved_by="policy:mock")


def test_simulate_mode_rejected_for_instagram():
    with pytest.raises(ValueError):
        Settings(platform=PlatformKind.INSTAGRAM, mode=Mode.SIMULATE).check_consistency()


@pytest.mark.parametrize("username,status,opted,review,ok", [
    ("mia", "none", False, "ok", True), ("kai_2010", "none", False, "ok", False), ("x2009x", "none", False, "ok", False),
    ("anna1983", "none", False, "ok", True), ("vanessa19832025", "none", False, "ok", True),
    ("mia", "sent", False, "ok", False), ("mia", "unknown", False, "ok", False), ("mia", "none", True, "ok", False),
    ("mia", "none", False, "quarantined", False),
])
def test_intro_eligibility(username, status, opted, review, ok):
    assert policy.intro_eligibility(make_settings(), username=username, intro_status=status, opted_out=opted, review_status=review)[0] is ok


@pytest.mark.parametrize("text,stop", [("please stop messaging me", True), ("leave me alone", True), ("not interested", True),
                                       ("don't text me again", True), ("I can't stop laughing", False), ("hey whats up", False)])
def test_disengagement(text, stop):
    assert policy.wants_to_disengage(text) is stop


@pytest.mark.parametrize("text,blocked", [("im 17", True), ("I'm 16 years old", True), ("i am 22", False), ("im 5 minutes away", False),
                                          ("I'm 92", True), ("im 19 lol", False)])
def test_age_blocks(text, blocked):
    assert policy.age_blocks_contact(make_settings(), text) is blocked


# --------------------------------------------------------------------- job queue
def _queue(capacity=100):
    db = Database(":memory:"); db.migrate()
    return db, JobQueue(db, LimitsConfig(queue_capacity=capacity))


def test_enqueue_dedups():
    _, q = _queue()
    assert q.enqueue("intro", "a", "intro:a")
    assert q.enqueue("intro", "a", "intro:a") is None


def test_queue_capacity_backpressure():
    _, q = _queue(capacity=2)
    q.enqueue("intro", "a", "k1")
    q.enqueue("intro", "b", "k2")
    with pytest.raises(QueueFull):
        q.enqueue("intro", "c", "k3")


def test_two_workers_cannot_claim_the_same_job(tmp_path):
    db = Database(tmp_path / "q.db"); db.migrate()
    q = JobQueue(db, LimitsConfig())
    q.enqueue("intro", "a", "k1")
    got, barrier = [], threading.Barrier(8)

    def worker(i):
        own = JobQueue(Database(tmp_path / "q.db"), LimitsConfig())   # separate connection per worker
        barrier.wait()
        j = own.claim(f"w{i}")
        if j:
            got.append(j.id)
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(got) == 1


def test_same_subject_serialized():
    _, q = _queue()
    q.enqueue("intro", "a", "k1")
    q.enqueue("reply", "a", "k2")
    q.enqueue("intro", "b", "k3")
    first = q.claim("w1")
    second = q.claim("w2")
    assert first.subject_key == "a" and second.subject_key == "b"
    assert q.claim("w3") is None


def test_lease_owner_enforced():
    _, q = _queue()
    q.enqueue("intro", "a", "k1")
    j = q.claim("w1")
    from agent.core.errors import LeaseLost
    with pytest.raises(LeaseLost):
        q.transition(j.id, JobState.INSPECTING, "x", owner="intruder")
    q.transition(j.id, JobState.QUEUED, "requeued by recovery")
    with pytest.raises(LeaseLost):                       # the original worker lost it too
        q.transition(j.id, JobState.INSPECTING, "x", owner="w1")


def test_stale_leases_requeue_before_side_effects_and_reconcile_during_send():
    db, q = _queue()
    a = q.enqueue("intro", "a", "k1"); q.claim("w1")
    b = q.enqueue("intro", "b", "k2"); q.claim("w1")
    q.transition(b, JobState.INSPECTING, "x", owner="w1")
    q.transition(b, JobState.REASONING, "x", owner="w1")
    q.transition(b, JobState.READY_TO_SEND, "x", owner="w1")
    q.transition(b, JobState.SENDING, "x", owner="w1")
    db.execute("UPDATE jobs SET lease_expires_at = ?", (iso(now() - timedelta(seconds=5)),))
    q.recover_stale_leases()
    assert q.get(a).state is JobState.QUEUED
    assert q.get(b).state is JobState.DELIVERY_UNKNOWN


def test_transitions_are_persisted_with_reasons():
    db, q = _queue()
    j = q.enqueue("intro", "a", "k1")
    q.claim("w1")
    reasons = [r["reason"] for r in db.all("SELECT reason FROM job_transitions WHERE job_id = ? ORDER BY id", (j,))]
    assert reasons == ["enqueued", "claimed by w1"]
