"""Follow-ups: who's waiting on you, hellos nobody answered, your own reminders. Snooze and Done hide them; nothing
is ever sent."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from assistant.app import Assistant, create_api
from assistant.config import AssistantSettings
from assistant.followups import followups, remind_due
from assistant.practice import PracticeLLM
from assistant.store import Store

NOW = datetime.now(timezone.utc).replace(microsecond=0)
ms = lambda dt: int(dt.timestamp() * 1000)
iso = lambda dt: dt.isoformat(timespec="seconds")


@pytest.fixture
def st(tmp_path):
    s = Store(tmp_path / "a.db")
    s.set("last_import", {"at": iso(NOW - timedelta(days=1))})

    def thread(tid, user, last_at, from_me, group=0, request=0):
        s.execute("INSERT INTO threads(id, title, username, other_name, is_group, is_request, last_at, last_from_me, message_count) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)", (tid, user, user, user.title(), group, request, ms(last_at), from_me))
        s.execute("INSERT INTO messages(thread_id, at, from_me, sender, text) VALUES (?, ?, ?, ?, ?)",
                  (tid, ms(last_at), from_me, "me" if from_me else user, f"hi from {user}"))

    thread("t1", "maya", NOW - timedelta(days=4), 0)          # waiting on you
    thread("t2", "ben", NOW - timedelta(hours=10), 0)         # too recent to nag
    thread("t3", "cal", NOW - timedelta(days=5), 1)           # you wrote last
    thread("t4", "grp", NOW - timedelta(days=5), 0, group=1)  # a group
    thread("t5", "old", NOW - timedelta(days=200), 0)         # history, not a follow-up
    thread("t6", "dee", NOW - timedelta(days=3), 0)           # a reply is already approved
    thread("t8", "ella", NOW - timedelta(days=3), 0)          # said they're 16: Convo never suggests them
    s.execute("INSERT INTO people(username, concern, concern_why) VALUES ('ella', 'high', 'said they''re 16')")
    thread("t9", "cash", NOW - timedelta(days=3), 0)          # a reply you skipped
    s.execute("INSERT INTO drafts(kind, username, thread_id, text, status, replying_at, created_at, updated_at) "
              "VALUES ('reply', 'cash', 't9', '', 'skipped', ?, ?, ?)", (ms(NOW - timedelta(days=3)), iso(NOW), iso(NOW)))
    thread("t10", "nope", NOW - timedelta(days=3), 0)         # on the never-message list
    s.execute("INSERT INTO drafts(kind, username, thread_id, text, status, replying_at, created_at, updated_at) "
              "VALUES ('reply', 'dee', 't6', 'see you', 'approved', ?, ?, ?)", (ms(NOW - timedelta(days=3)), iso(NOW), iso(NOW)))

    def hello(user, sent, status="sent"):
        s.execute("INSERT INTO drafts(kind, username, text, status, created_at, updated_at, sent_at) VALUES ('hello', ?, 'hey :)', ?, ?, ?, ?)",
                  (user, status, iso(sent), iso(sent), iso(sent)))
    hello("quiet", NOW - timedelta(days=10))                  # no answer: a follow-up
    hello("answered", NOW - timedelta(days=10))
    thread("t7", "answered", NOW - timedelta(days=8), 0)      # ...they wrote back after the hello
    hello("fresh", NOW - timedelta(days=2))                   # too soon
    hello("after", NOW - timedelta(hours=12))                 # sent after the newest download (and too soon)
    return s


def test_it_finds_the_right_people(st):
    f = followups(st, now=NOW, never={"nope"})
    who = {(i["kind"], i["username"]) for i in f["items"]}
    assert ("waiting", "maya") in who and ("waiting", "answered") in who
    assert not {u for k, u in who if k == "waiting"} & {"ben", "cal", "grp", "old", "dee", "ella", "cash", "nope"}
    assert {u for k, u in who if k == "no_reply"} == {"quiet"}
    assert f["counts"]["no_reply"] == 1


def test_snooze_done_and_reminders_through_the_api(st, tmp_path):
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "p.json", never_list_path=tmp_path / "n.txt")
    a = Assistant(s, store=st, make_web=lambda: None, llm=PracticeLLM(), notify=lambda *x: None, env_path=tmp_path / ".env")
    c = TestClient(create_api(a, "t"), base_url="http://127.0.0.1")
    h = {"X-Agent-Token": "t"}
    items = c.get("/api/hub/followups", headers=h).json()["items"]
    maya = next(i for i in items if i["username"] == "maya")
    assert c.post(f"/api/hub/followups/{maya['key']}/snooze", json={"days": 3}, headers=h).status_code == 200
    assert "maya" not in {i["username"] for i in c.get("/api/hub/followups", headers=h).json()["items"]}
    assert c.post(f"/api/hub/followups/{maya['key']}/undo", headers=h).status_code == 200
    assert "maya" in {i["username"] for i in c.get("/api/hub/followups", headers=h).json()["items"]}
    # A reminder for the past shows now; one for later is listed as upcoming.
    past = iso(datetime.now(timezone.utc) - timedelta(hours=1))
    r = c.post("/api/hub/reminders", json={"username": "@maya", "note": "ask how the show went", "due_at": past}, headers=h).json()
    c.post("/api/hub/reminders", json={"note": "post the bake video", "due_at": "2099-01-01"}, headers=h)
    f = c.get("/api/hub/followups", headers=h).json()
    assert any(i["kind"] == "reminder" and i["text"] == "ask how the show went" and i["username"] == "maya" for i in f["items"])
    assert [u["text"] for u in f["upcoming"]] == ["post the bake video"]
    assert c.post(f"/api/hub/followups/reminder:{r['id']}/done", headers=h).status_code == 200
    assert not any(i["kind"] == "reminder" for i in c.get("/api/hub/followups", headers=h).json()["items"])
    assert c.post("/api/hub/reminders", json={"note": " ", "due_at": "2099-01-01"}, headers=h).status_code == 400
    assert st.scalar("SELECT COUNT(*) FROM drafts WHERE status = 'sent'") == 4        # nothing new was sent


def test_a_due_reminder_notifies_once(st):
    st.execute("INSERT INTO reminders(username, note, due_at, created_at) VALUES ('maya', 'call', ?, ?)",
               (iso(datetime.now(timezone.utc) - timedelta(minutes=1)), iso(NOW)))
    told = []
    assert remind_due(st, lambda title, text: told.append(text)) == 1
    assert remind_due(st, lambda title, text: told.append(text)) == 0
    assert told == ["@maya: call"]
