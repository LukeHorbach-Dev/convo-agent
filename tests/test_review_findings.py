"""Regression tests for defects found in the release-readiness review (docs/REVIEW.md).

Each test was written first, observed failing against the pre-review code, then the defect was fixed.
"""
from __future__ import annotations

import subprocess
from datetime import timedelta

import pytest

from agent.app import build_app
from agent.core.config import Mode, PlatformKind, Settings
from agent.core.db import iso, now
from agent.core.recovery import RunState
from agent.intel.llm import FakeLLM
from agent.platform.mock import MockPlatform

from .conftest import ROOT, job_states, make_settings, settle


class RealishPlatform(MockPlatform):
    is_real = True


def _backdate_progress(db, seconds: float) -> None:
    db.execute("UPDATE jobs SET progress_at = ? WHERE lease_owner IS NOT NULL", (iso(now() - timedelta(seconds=seconds)),))


# R1 (high): a send that outlives its lease. The watchdog moves the job to delivery_unknown while the worker
# is still inside platform.send. The worker must not crash the app, and the delivered message must end as sent.
def test_slow_send_outliving_lease_is_reconciled_not_stuck(app, world, monkeypatch):
    world.new_follower("mia")
    real_send = app.platform.send

    def slow_send(thread_key, text, op):
        _backdate_progress(app.db, app.settings.limits.stall_s + 10)
        app.engine.watchdog()                       # the watchdog thread fires during the hung send
        return real_send(thread_key, text, op)
    monkeypatch.setattr(app.platform, "send", slow_send)
    app.engine.run_once()
    monkeypatch.undo()
    settle(app.engine, 3)
    assert app.engine.controls.run_state is RunState.RUNNING
    assert len(world.sent_log) == 1
    assert app.db.scalar("SELECT state FROM outbox") == "sent"
    assert app.db.scalar("SELECT state FROM jobs") == "completed"


# R2 (medium): a slow model call outlives the lease; the worker must abandon the job, not quarantine the app.
def test_lease_lost_before_send_abandons_job_without_stopping_app(app, world, llm):
    world.new_follower("mia"); app.engine.run_once()
    world.incoming("mia", "hey how was your week")

    def slow_model(system, user):
        _backdate_progress(app.db, app.settings.limits.stall_s + 10)
        app.engine.watchdog()
        return FakeLLM.default_responder(system, user)
    llm.queue.append(slow_model)
    app.engine.run_once()
    assert app.engine.controls.run_state is RunState.RUNNING
    settle(app.engine, 3)
    assert job_states(app.db, "reply")["mia"] == "completed"
    assert len(world.sent_log) == 2                 # intro + exactly one reply


@pytest.fixture
def real_app(world, llm, db):
    return build_app(make_settings(mode=Mode.EXECUTE), db=db, platform=RealishPlatform(world), world=world, llm=llm)


# R3 (high): an approval must not outlive the context it was given in.
def test_approved_reply_not_sent_after_person_opts_out(real_app, world):
    a = real_app
    world.new_follower("mia"); a.engine.run_once()
    a.engine.approve_draft(a.db.scalar("SELECT id FROM drafts")); settle(a.engine, 2)
    world.incoming("mia", "hey whats up")
    a.engine.run_once()
    reply_draft = a.db.scalar("SELECT d.id FROM drafts d JOIN jobs j ON j.id = d.job_id WHERE j.kind = 'reply'")
    world.incoming("mia", "actually please stop messaging me")
    a.engine.approve_draft(reply_draft)            # operator approves the stale draft
    settle(a.engine, 3)
    assert [t for u, t in world.sent_log if u == "mia"] == [world.sent_log[0][1]]     # only the intro


def test_approved_reply_superseded_by_newer_message(real_app, world):
    a = real_app
    world.new_follower("mia"); a.engine.run_once()
    a.engine.approve_draft(a.db.scalar("SELECT id FROM drafts")); settle(a.engine, 2)
    world.incoming("mia", "what do you do?")
    a.engine.run_once()
    old = a.db.scalar("SELECT d.id FROM drafts d JOIN jobs j ON j.id = d.job_id WHERE j.kind = 'reply'")
    world.incoming("mia", "also do you ski?")
    a.engine.approve_draft(old)
    settle(a.engine, 3)
    assert len(world.sent_log) == 1                 # the stale reply was not sent
    assert a.db.scalar("SELECT state FROM jobs WHERE id = (SELECT job_id FROM drafts WHERE id = ?)", (old,)) == "skipped"


def test_expired_approval_is_not_used(real_app, world):
    a = real_app
    world.new_follower("mia"); a.engine.run_once()
    a.engine.approve_draft(a.db.scalar("SELECT id FROM drafts"))
    a.db.execute("UPDATE outbox SET approved_at = ?", (iso(now() - timedelta(hours=a.settings.policy.approval_ttl_hours + 1)),))
    settle(a.engine, 3)
    assert world.sent_log == []
    assert job_states(a.db)["mia"] == "needs_review"


# R4 (high): the "mock" web platform must not be pointable at a real site, where it would send without approval.
@pytest.mark.parametrize("url", ["https://www.instagram.com", "http://10.0.0.5:8899", "http://evil.example:80"])
def test_mock_site_url_must_be_local(url):
    with pytest.raises(ValueError):
        Settings(platform=PlatformKind.MOCK_WEB, mock_site_url=url).check_consistency()


# R5 (medium): approvals are bound to recipient and content, and only accepted when the job is waiting for one.
def test_tampered_outbox_text_is_refused(real_app, world):
    a = real_app
    world.new_follower("mia"); a.engine.run_once()
    a.engine.approve_draft(a.db.scalar("SELECT id FROM drafts"))
    a.db.execute("UPDATE outbox SET text = 'something I never approved'")
    settle(a.engine, 3)
    assert world.sent_log == []


def test_tampered_outbox_recipient_is_refused(real_app, world):
    a = real_app
    world.new_follower("mia"); world.new_follower("other"); a.engine.run_once()
    draft = a.db.scalar("SELECT id FROM drafts WHERE target = 'mia'")
    a.engine.approve_draft(draft)
    a.db.execute("UPDATE outbox SET target = 'other' WHERE draft_id = ?", (draft,))
    settle(a.engine, 3)
    assert not any(u == "other" for u, _ in world.sent_log) and not any(u == "mia" for u, _ in world.sent_log)


def test_approval_refused_while_job_in_flight(real_app, world):
    a = real_app
    world.new_follower("mia"); a.engine.run_once()
    d = a.db.scalar("SELECT id FROM drafts")
    a.engine.approve_draft(d)
    with pytest.raises(Exception):
        a.engine.approve_draft(d, "Hey, hows your week going ?")   # second approval races the first


def test_superseded_draft_cannot_be_approved(real_app, world, llm):
    a = real_app
    world.new_follower("mia"); a.engine.run_once()
    a.engine.approve_draft(a.db.scalar("SELECT id FROM drafts")); settle(a.engine, 2)
    world.incoming("mia", "hey")
    llm.queue.append({"reply": "I'm a pilot", "intent": "x", "facts_used": [], "needs_human": False, "reason": "", "memory_notes": []})
    a.engine.run_once()                                          # escalated draft
    first = a.db.scalar("SELECT d.id FROM drafts d JOIN jobs j ON j.id = d.job_id WHERE j.kind = 'reply'")
    job = a.db.scalar("SELECT job_id FROM drafts WHERE id = ?", (first,))
    a.engine.jobs.transition(job, __import__("agent.core.states", fromlist=["x"]).JobState.QUEUED, "operator retry")
    a.engine.run_once()                                          # redraft creates a second draft for the same job
    assert a.db.scalar("SELECT status FROM drafts WHERE id = ?", (first,)) == "superseded"
    with pytest.raises(Exception):
        a.engine.approve_draft(first)


# R6 (medium): approving in draft mode must park the job, not re-run the platform every tick.
def test_draft_mode_approval_does_not_churn(world, llm, db):
    a = build_app(make_settings(mode=Mode.DRAFT), db=db, platform=MockPlatform(world), world=world, llm=llm)
    world.new_follower("mia"); a.engine.run_once()
    a.engine.approve_draft(a.db.scalar("SELECT id FROM drafts"))
    settle(a.engine, 2)
    calls = a.platform.calls
    settle(a.engine, 4)
    assert world.sent_log == []
    assert job_states(a.db)["mia"] == "blocked"
    assert a.platform.calls - calls <= 4 * 2          # ingestion only (followers + threads), no per-tick send attempts


# R7 (medium): rejecting an intro must not leave the person permanently "pending".
def test_rejected_intro_is_marked_skipped(real_app, world):
    a = real_app
    world.new_follower("mia"); a.engine.run_once()
    a.engine.reject_draft(a.db.scalar("SELECT id FROM drafts"))
    assert a.db.scalar("SELECT intro_status FROM accounts WHERE username = 'mia'") == "skipped"


# R8 (low): retention must run even while the application is paused.
def test_retention_runs_while_paused(app):
    app.db.execute("INSERT INTO memory(id, target, kind, content, source, created_at) VALUES ('m1', 'x', 'their_statement', 'old', 's', ?)",
                   (iso(now() - timedelta(days=app.settings.policy.retention_days + 1)),))
    app.engine.controls.pause("operator break")
    app.engine.run_once()
    assert app.db.scalar("SELECT COUNT(*) FROM memory") == 0


# R9 (low): personal data, logs and credentials are never tracked by git.
@pytest.mark.parametrize("path", ["data/profile.json", "data/agent.db", "data/agent.log", "data/dashboard-token",
                                  "data/scheduler.lock", "data/browser-profile/Default/Cookies", ".env"])
def test_sensitive_paths_are_git_ignored(path, tmp_path):
    # Evaluate the project's .gitignore in a throwaway repo, so the test works from any copy of the source.
    (tmp_path / ".gitignore").write_text((ROOT / ".gitignore").read_text())
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    r = subprocess.run(["git", "check-ignore", "-q", path], cwd=tmp_path)
    assert r.returncode == 0, f"{path} is not ignored"


def test_test_fixture_profile_is_synthetic():
    text = (ROOT / "tests/fixtures/profile.json").read_text()
    for personal in ("Sydney", "Hakuba", "Korea", "Silvia", "Luke"):
        assert personal not in text


def test_cli_refuses_non_local_mock_site_before_starting_anything(tmp_path):
    import os
    env = {**os.environ, "AGENT_PLATFORM": "mock_web", "AGENT_MOCK_SITE_URL": "https://www.instagram.com",
           "AGENT_DATA_DIR": str(tmp_path), "AGENT_DB_PATH": str(tmp_path / "a.db")}
    r = subprocess.run([__import__("sys").executable, "-m", "agent", "run", "--headless"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode != 0 and "configuration error: mock_site_url must be a local http URL" in r.stderr
    assert not (tmp_path / "scheduler.lock").exists()


@pytest.mark.parametrize("path", ["data/never.txt", "data/intro-list.txt"])
def test_personal_lists_are_git_ignored(path, tmp_path):
    (tmp_path / ".gitignore").write_text((ROOT / ".gitignore").read_text())
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    assert subprocess.run(["git", "check-ignore", "-q", path], cwd=tmp_path).returncode == 0
