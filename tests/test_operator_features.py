"""Operator features: never-message list, list import, plain-English translation, and dashboard XSS safety."""
from __future__ import annotations

import socket
import threading
import time

import pytest

from agent.app import build_app
from agent.core.config import Mode
from agent.platform.mock import MockPlatform

from .conftest import job_states, make_settings, settle

SPANISH = {"reply": "Hola, soy Alex, que tal tu semana?", "intent": "greeting", "facts_used": ["f_name"], "needs_human": False,
           "reason": "", "memory_notes": [], "language": "Spanish", "their_message_english": "Hi, how are you?",
           "reply_english": "Hi, I'm Alex, how is your week?"}


class RealishPlatform(MockPlatform):
    is_real = True


def _app_with_never(tmp_path, world, llm, db, names, mode=Mode.SIMULATE, real=False):
    s = make_settings(mode=mode)
    f = tmp_path / "never.txt"
    f.write_text("# people I never message\n" + "\n".join(names) + "\n")
    s.policy.never_list_path = f
    return build_app(s, db=db, platform=(RealishPlatform if real else MockPlatform)(world), world=world, llm=llm)


def test_never_list_blocks_intro_and_replies(tmp_path, world, llm, db):
    app = _app_with_never(tmp_path, world, llm, db, ["@Blocked.Person"])
    world.new_follower("blocked.person")
    world.new_follower("ok.person")
    app.engine.run_once()
    world.incoming("blocked.person", "hey")
    settle(app.engine, 3)
    assert [u for u, _ in world.sent_log] == ["ok.person"]
    assert app.db.scalar("SELECT COUNT(*) FROM jobs WHERE subject_key = 'blocked.person'") == 0


def test_adding_someone_to_never_list_cancels_their_approved_message(tmp_path, world, llm, db):
    app = _app_with_never(tmp_path, world, llm, db, [], mode=Mode.EXECUTE, real=True)
    world.new_follower("mia"); app.engine.run_once()
    app.engine.approve_draft(app.db.scalar("SELECT id FROM drafts"))
    app.settings.policy.excluded_usernames.append("mia")
    settle(app.engine, 3)
    assert world.sent_log == []
    assert app.db.scalar("SELECT state FROM outbox") == "cancelled"


def test_import_list_queues_with_all_policy_checks(world, llm, db):
    s = make_settings()
    s.policy.excluded_usernames = ["never.me"]
    app = build_app(s, db=db, platform=MockPlatform(world), world=world, llm=llm)
    for u in ("alice", "bob", "kid_2010", "never.me"):
        world.add_account(u)
    out = app.engine.import_intro_list(["alice", "@Bob", "kid_2010", "never.me", "not a username!", "", "alice"])
    assert out == {"queued": 2, "already_known": 1, "not_eligible": 2, "invalid": 1}
    assert app.engine.import_intro_list(["alice"])["already_known"] == 1          # never a second intro
    settle(app.engine, 3)
    assert sorted(u for u, _ in world.sent_log) == ["alice", "bob"]
    assert app.db.scalar("SELECT follower_source FROM accounts WHERE username = 'alice'") == "imported"


def test_import_list_on_real_platform_waits_for_approval(world, llm, db):
    app = build_app(make_settings(mode=Mode.EXECUTE), db=db, platform=RealishPlatform(world), world=world, llm=llm)
    world.add_account("alice")
    app.engine.import_intro_list(["alice"])
    settle(app.engine, 3)
    assert world.sent_log == [] and job_states(app.db)["alice"] == "awaiting_approval"


def test_plain_english_translation_is_stored_with_the_draft(app, world, llm):
    world.new_follower("sofia"); app.engine.run_once()
    world.incoming("sofia", "Hola, que tal?")
    llm.queue.append(SPANISH)
    app.engine.run_once()
    import json
    ctx = json.loads(app.db.scalar("SELECT context FROM drafts d JOIN jobs j ON j.id = d.job_id WHERE j.kind = 'reply'"))
    assert ctx["language"] == "Spanish" and ctx["their_message_english"] == "Hi, how are you?"
    assert ctx["reply_english"].startswith("Hi, I'm Alex")
    system, _ = llm.calls[-1]
    assert "language of their latest message" in system and "dyslexic" in system


@pytest.mark.browser
def test_dashboard_does_not_execute_message_content(world, llm, db):
    """A hostile message must render as text, never as markup or script (it reaches the dashboard via the
    model's plain-English field and the draft text)."""
    import uvicorn
    from playwright.sync_api import sync_playwright
    from agent.web.dashboard import create_dashboard

    app = build_app(make_settings(mode=Mode.EXECUTE), db=db, platform=RealishPlatform(world), world=world, llm=llm)
    world.new_follower("eve"); app.engine.run_once()
    app.engine.approve_draft(app.db.scalar("SELECT id FROM drafts")); settle(app.engine, 2)
    evil = """it's "fine" '><img src=x onerror="window.__xss=1"><script>window.__xss=2</script>"""
    world.incoming("eve", evil)
    llm.queue.append({**SPANISH, "their_message_english": evil, "reply": "ok", "facts_used": [], "needs_human": True})
    app.engine.run_once()
    with socket.socket() as sk:
        sk.bind(("127.0.0.1", 0)); port = sk.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_dashboard(app, "tok"), host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True); t.start()
    while not server.started:
        time.sleep(0.05)
    try:
        with sync_playwright() as p:
            b = p.chromium.launch()
            pg = b.new_page()
            pg.goto(f"http://127.0.0.1:{port}/")
            pg.evaluate("() => sessionStorage.setItem('agentToken', 'tok')")
            pg.reload()
            pg.wait_for_selector("[data-say]")
            pg.wait_for_timeout(300)
            assert pg.evaluate("() => window.__xss") is None
            assert pg.locator("[data-say]").first.get_attribute("data-say") == evil
            assert evil in pg.locator(".plain").first.inner_text()
            b.close()
    finally:
        server.should_exit = True
        t.join(5)


def test_waiting_hellos_are_capped_and_the_rest_wait_their_turn(world, llm, db):
    """With a long list, only a few hellos are prepared ahead of your approval; approving one lets the next start."""
    s = make_settings(mode=Mode.EXECUTE)
    s.limits.max_waiting_intros = 2
    app = build_app(s, db=db, platform=RealishPlatform(world), world=world, llm=llm)
    for i in range(5):
        world.add_account(f"friend{i}")
    app.engine.import_intro_list([f"friend{i}" for i in range(5)])
    settle(app.engine, 4)
    states = job_states(app.db)
    assert sorted(states.values()).count("awaiting_approval") == 2 and list(states.values()).count("queued") == 3
    assert world.sent_log == []


def test_list_is_reread_only_every_interval(world, llm, db):
    s = make_settings()
    s.list_check_interval_s = 3600
    calls = []
    plat = MockPlatform(world)
    real_poll = plat.poll_followers
    plat.poll_followers = lambda: calls.append(1) or real_poll()
    app = build_app(s, db=db, platform=plat, world=world, llm=llm)
    for _ in range(3):
        app.engine.run_once()
    assert len(calls) == 1


def test_without_an_ai_key_real_replies_are_left_for_you_to_write(world, db):
    """On a real platform with the fake model, a reply is never canned test text: the box is empty, the card
    shows what they said, and nothing is sent until you type something and press Send."""
    from agent.intel.llm import FakeLLM
    app = build_app(make_settings(mode=Mode.EXECUTE), db=db, platform=RealishPlatform(world), world=world, llm=FakeLLM())
    world.new_follower("mia"); app.engine.run_once()
    app.engine.approve_draft(app.db.scalar("SELECT id FROM drafts")); settle(app.engine, 2)
    world.incoming("mia", "how was your week?")
    settle(app.engine, 2)
    d = app.db.one("SELECT d.*, j.state FROM drafts d JOIN jobs j ON j.id = d.job_id WHERE j.kind = 'reply'")
    assert d["text"] == "" and d["state"] == "needs_review"
    assert "how was your week" in d["context"]
    assert len(world.sent_log) == 1
    app.engine.approve_draft(d["id"], "Pretty good, busy with uni")
    settle(app.engine, 2)
    assert world.sent_log[-1] == ("mia", "Pretty good, busy with uni")
