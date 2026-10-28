"""Randomized fault injection. Invariants checked against the mock platform's ground truth:

  1. No outbox operation is ever delivered twice.
  2. A delivered operation is never recorded as failed (that would make it eligible for resend).
  3. Nobody gets more than one introduction.
  4. After restart recovery, no outbox row is left in 'attempting'.
  5. Durable work is never silently lost: every follower ends with a job or a recorded reason.
"""
from __future__ import annotations

import random
import time
from collections import Counter

import pytest

from agent.app import build_app
from agent.core.db import Database
from agent.engine.engine import Engine
from agent.intel.llm import FakeLLM
from agent.platform.mock import MockPlatform, MockWorld

from .conftest import Crash, make_settings

SEEDS = range(int(__import__("os").environ.get("CHAOS_SEEDS", "40")))


class ChaosPlatform(MockPlatform):
    def __init__(self, world, rng):
        super().__init__(world)
        self.rng = rng
        self.delivered_ops: list[str] = []
        self.crash_rate = 0.0
        self.current_op: str | None = None
        self.engine = None              # set by the harness so the watchdog can fire mid-send
        self.watchdog_rate = 0.0

    def send(self, thread_key, text, operation_id):
        r = self.rng.random()
        if r < self.crash_rate / 2:
            raise Crash()                                   # dies before delivering
        if self.engine is not None and self.rng.random() < self.watchdog_rate:
            # The send hangs past its lease: the watchdog thread takes the job over while we're still sending.
            self.engine.db.execute("UPDATE jobs SET progress_at = '2000-01-01T00:00:00.000+00:00' WHERE lease_owner IS NOT NULL")
            self.engine.watchdog()
        self.current_op = operation_id
        try:
            result = super().send(thread_key, text, operation_id)
        finally:
            self.current_op = None
        if r < self.crash_rate:
            raise Crash()                                   # dies right after delivering
        return result


def run_chaos(seed: int, tmp_path) -> dict:
    rng = random.Random(seed)
    s = make_settings()
    s.circuit.cooldown_s = 0.01
    s.limits.daily_send_limit = 1000
    world = MockWorld("lukehorbach2")
    platform = ChaosPlatform(world, rng)
    original_deliver = world.deliver

    def deliver_tracking(thread_key, text):
        # Ground truth: record the operation at the moment the platform actually delivers it.
        platform.delivered_ops.append(platform.current_op)
        return original_deliver(thread_key, text)
    world.deliver = deliver_tracking
    db = Database(tmp_path / f"chaos{seed}.db"); db.migrate()
    llm = FakeLLM()
    app = build_app(s, db=db, platform=platform, world=world, llm=llm)
    engine = app.engine
    platform.engine = engine
    platform.watchdog_rate = 0.1
    users = [f"user{i}" for i in range(8)]
    crashes = 0
    for tick in range(30):
        f = world.faults
        f.outage = rng.random() < 0.08
        f.selector_drift_for = {u for u in users if rng.random() < 0.05}
        f.send_mode = rng.choice(["normal"] * 5 + ["fail", "unknown_delivered", "unknown_not_delivered", "crash_after_delivery"])
        f.duplicate_follower_events = rng.random() < 0.2
        platform.crash_rate = 0.15
        if rng.random() < 0.15:
            llm.queue.append(rng.choice(["timeout", "malformed"]))
        if users and rng.random() < 0.5:
            world.new_follower(users.pop(0))
        known = [u for u in world.accounts if world.thread_for(u, create=False)]
        if known and rng.random() < 0.4:
            world.incoming(rng.choice(known), rng.choice(["hey", "how are you", "just back from skiing", "what do you do?"]))
        try:
            engine.run_once()
        except Crash:
            crashes += 1
            engine = Engine(app.settings, db, platform, engine.drafter, app.profile)
            platform.engine = engine
            engine.startup_recovery()
            assert db.scalar("SELECT COUNT(*) FROM outbox WHERE state = 'attempting'") == 0
        if engine.controls.run_state.value != "running" and not engine.controls.emergency_stopped:
            engine.controls.resume()
        time.sleep(0.003)
    # Calm period: no faults, let everything settle.
    world.faults.outage = False
    world.faults.selector_drift_for = set()
    world.faults.send_mode = "normal"
    platform.crash_rate = 0.0
    platform.watchdog_rate = 0.0
    for _ in range(25):
        if engine.controls.run_state.value != "running":
            engine.controls.resume()
        engine.run_once()
        time.sleep(0.003)
    return {"db": db, "platform": platform, "world": world, "crashes": crashes, "profile": app.profile}


@pytest.mark.parametrize("seed", SEEDS)
def test_chaos_invariants(seed, tmp_path):
    r = run_chaos(seed, tmp_path)
    db, platform, world = r["db"], r["platform"], r["world"]
    ops = Counter(platform.delivered_ops)
    # 1. no operation delivered twice
    assert all(n == 1 for n in ops.values()), ops
    # 2. delivered ops are never recorded as failed/pending (which would allow a resend)
    for op in ops:
        state = db.scalar("SELECT state FROM outbox WHERE id = ?", (op,))
        assert state in ("sent", "unknown"), (op, state)
    # 3. at most one intro per person
    intros = Counter(u for u, t in world.sent_log if t in r["profile"].intro_templates)
    assert all(n <= 1 for n in intros.values()), intros
    # 4. no attempts left dangling
    assert db.scalar("SELECT COUNT(*) FROM outbox WHERE state = 'attempting'") == 0
    # 5. every follower has a job or a recorded reason (nothing silently dropped)
    for u in world.accounts:
        has_job = db.scalar("SELECT COUNT(*) FROM jobs WHERE subject_key = ? AND kind = 'intro'", (u,))
        reason = db.scalar("SELECT COUNT(*) FROM audit WHERE action = 'intro_not_eligible' AND detail LIKE ?", (f"%{u}%",))
        intro = db.scalar("SELECT intro_status FROM accounts WHERE username = ?", (u,))
        assert has_job or reason or intro in ("skipped", "sent"), u
    # Sent outbox rows match ground truth exactly.
    sent_rows = {r["id"] for r in db.all("SELECT id FROM outbox WHERE state = 'sent'")}
    assert sent_rows <= set(ops)


def test_chaos_actually_exercises_faults(tmp_path):
    """Guard against a chaos suite that passes because nothing happened."""
    totals = Counter()
    for seed in range(10):
        r = run_chaos(seed, tmp_path)
        db = r["db"]
        totals["crashes"] += r["crashes"]
        totals["reconciled"] += db.scalar("SELECT COUNT(*) FROM delivery_attempts WHERE error LIKE '%reconciled%'")
        totals["unknown_attempts"] += db.scalar("SELECT COUNT(*) FROM delivery_attempts WHERE error IS NOT NULL")
        totals["retries"] += db.scalar("SELECT COUNT(*) FROM recovery_events WHERE decision = 'retry'")
        totals["delivered"] += len(r["platform"].delivered_ops)
        totals["watchdog_mid_send"] += db.scalar("SELECT COUNT(*) FROM delivery_attempts WHERE error LIKE '%watchdog%'")
    assert totals["watchdog_mid_send"] >= 1, totals
    assert totals["crashes"] >= 5 and totals["reconciled"] >= 3 and totals["retries"] >= 5 and totals["delivered"] >= 30, totals
