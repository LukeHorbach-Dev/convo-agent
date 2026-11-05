"""The Command Centre's data (assistant/hub.py) and the safety of Luke's existing data through upgrades."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.intel.llm import FakeLLM
from assistant.app import Assistant, create_api
from assistant.config import AssistantSettings
from assistant.export import read_export
from assistant.store import Store
from tests.test_assistant import make_export
from tests.test_eyes import add_draft

H = {"X-Agent-Token": "tok"}


@pytest.fixture
def app(tmp_path):
    (tmp_path / "profile.json").write_text((Path(__file__).parent / "fixtures" / "profile.json").read_text())
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "profile.json",
                          never_list_path=tmp_path / "never.txt", downloads_dir=tmp_path)
    a = Assistant(s, store=Store(tmp_path / "assistant.db"), make_web=lambda: None, llm=FakeLLM(), notify=lambda *x: None,
                  env_path=tmp_path / ".env")
    c = TestClient(create_api(a, "tok"), base_url="http://127.0.0.1")
    return a, c, tmp_path


def load(a, tmp_path):
    from assistant.export import apply_export
    from assistant.hub import after_download
    summary = apply_export(a.store, read_export([make_export(tmp_path)]), "lukehorbach2")
    after_download(a.store, "lukehorbach2", summary)


def test_an_older_database_is_backed_up_before_it_is_upgraded_and_nothing_in_it_changes(tmp_path):
    db = tmp_path / "assistant.db"
    old = sqlite3.connect(db)          # a 4.1 database: no schema_version, a queue of hellos
    old.executescript("""CREATE TABLE drafts (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL, username TEXT,
        thread_id TEXT, other_name TEXT, replying_to TEXT, replying_at INTEGER, text TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'draft', note TEXT, context TEXT NOT NULL DEFAULT '{}', tries INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, sent_at TEXT);""")
    old.executemany("INSERT INTO drafts(kind, username, text, status, created_at, updated_at) VALUES ('hello', ?, 'Hey :)', 'draft', 'x', 'x')",
                    [(f"person{i}",) for i in range(1886)])
    old.commit()
    old.close()
    st = Store(db)
    backups = st.backups()
    assert len(backups) == 1 and "before-update-1" in backups[0]["name"]
    copy = sqlite3.connect(tmp_path / "backups" / backups[0]["name"])
    assert copy.execute("SELECT COUNT(*) FROM drafts").fetchone()[0] == 1886
    assert st.scalar("SELECT COUNT(*) FROM drafts WHERE kind = 'hello' AND status = 'draft'") == 1886   # the queue, untouched
    from assistant.store import MIGRATIONS
    assert st.get("schema_version") == MIGRATIONS[-1][0]
    Store(db)                                                       # opening again: nothing more to do, no new backup
    assert len(st.backups()) == 1


def test_a_5_0_database_is_backed_up_before_the_follow_ups_step(tmp_path):
    """5.0 databases are at step 1; opening them in 5.1 backs up first, then adds the reminders tables."""
    from assistant.store import MIGRATIONS
    db = tmp_path / "assistant.db"
    st = Store(db)
    st.execute("INSERT INTO drafts(kind, username, text, status, created_at, updated_at) VALUES ('hello', 'x', 'Hey', 'draft', 'x', 'x')")
    st.execute("UPDATE state SET value = '1' WHERE key = 'schema_version'")
    st.execute("DROP TABLE reminders")
    st.execute("DROP TABLE followups")
    st.close()
    st = Store(db)
    assert any("before-update-2" in b["name"] for b in st.backups())
    assert st.get("schema_version") == MIGRATIONS[-1][0]
    assert st.scalar("SELECT COUNT(*) FROM reminders") == 0 and st.scalar("SELECT COUNT(*) FROM drafts") == 1


def test_overview_uses_only_real_data_and_says_what_is_missing(app):
    a, c, tmp_path = app
    load(a, tmp_path)
    add_draft(a.store, "hello", "new.friend", "Hey :)", status="draft")
    o = c.get("/api/hub/overview", headers=H).json()
    assert o["kpis"]["followers"]["value"] == 2 and o["kpis"]["followers"]["source"] == "Instagram download"
    assert o["kpis"]["followers"]["change_7d"] is None                  # one measurement: no change to report yet
    assert o["kpis"]["views"] is None and "stages 4 and 5" in o["kpis"]["unavailable"]
    assert o["hello"]["waiting_approval"] == 1 and len(o["activity_series"]) == 30
    assert sum(d["received"] for d in o["activity_series"]) >= 2
    assert any(s["action"] == "open_hellos" for s in o["next_steps"])
    assert {p["id"] for p in o["platforms"]} == {"instagram", "tiktok", "youtube"}
    assert o["accounts"][0]["role"] == "main" and o["accounts"][0]["handle"] == "lukehorbach2"


def test_hello_reply_rate_counts_people_who_wrote_back_after_a_hello(app):
    a, c, tmp_path = app
    load(a, tmp_path)
    a.store.execute("INSERT INTO drafts(kind, username, text, status, created_at, updated_at, sent_at) VALUES "
                    "('hello', 'anna.walsh', 'Hey', 'sent', 'x', 'x', '2020-01-01T00:00:00+00:00')")
    a.store.execute("INSERT INTO drafts(kind, username, text, status, created_at, updated_at, sent_at) VALUES "
                    "('hello', 'new.friend', 'Hey', 'sent', 'x', 'x', '2020-01-01T00:00:00+00:00')")
    h = c.get("/api/hub/hello", headers=H).json()
    assert h["sent"] == 2 and h["replied"] == 1 and h["reply_rate"] == 0.5


def test_conversations_list_and_detail(app):
    a, c, tmp_path = app
    load(a, tmp_path)
    r = c.get("/api/hub/conversations?show=waiting", headers=H).json()
    ids = [t["id"] for t in r["items"]]
    assert "annawalsh_10224" in ids and "miaskis_555" not in ids        # mia: Luke wrote last
    d = c.get("/api/hub/conversations/annawalsh_10224", headers=H).json()
    assert d["messages"][-1]["text"].startswith("I’m good") and d["thread"]["username"] == "anna.walsh"
    assert c.get("/api/hub/conversations?q=anna", headers=H).json()["total"] == 1
    assert c.get("/api/hub/conversations/nope", headers=H).status_code == 404


def test_launch_network_accounts_and_groups(app):
    a, c, _ = app
    g = c.post("/api/hub/groups", headers=H, json={"name": "Football clips", "color": "#22d3ee"}).json()
    assert c.post("/api/hub/groups", headers=H, json={"name": "football clips"}).status_code == 409
    r = c.post("/api/hub/accounts", headers=H, json={"platform": "tiktok", "handle": "@luke.kicks", "niche": "Football",
                                                     "purpose": "Short football clips", "group_id": g["id"],
                                                     "referral_link": "https://bit.ly/x"})
    acc = r.json()
    assert r.status_code == 200 and acc["handle"] == "luke.kicks" and acc["group"]["name"] == "Football clips"
    assert acc["status"] == "not_connected" and acc["followers"] is None                 # no numbers made up
    assert c.post("/api/hub/accounts", headers=H, json={"platform": "tiktok", "handle": "luke.kicks"}).status_code == 409
    assert c.post("/api/hub/accounts", headers=H, json={"platform": "myspace", "handle": "x"}).status_code == 400
    assert c.post("/api/hub/accounts", headers=H, json={"platform": "instagram", "handle": "bad handle!"}).status_code == 400
    assert c.post("/api/hub/accounts", headers=H, json={"platform": "youtube", "handle": "chan", "referral_link": "ftp://x"}).status_code == 400
    for i in range(9):
        assert c.post("/api/hub/accounts", headers=H, json={"platform": "instagram", "handle": f"luke.extra{i}"}).status_code == 200
    assert "up to 10" in c.post("/api/hub/accounts", headers=H, json={"platform": "instagram", "handle": "one.too.many"}).json()["detail"]
    e = c.patch(f"/api/hub/accounts/{acc['id']}", headers=H, json={"purpose": "Match highlights", "group_id": None}).json()
    assert e["purpose"] == "Match highlights" and e["group"] is None
    main = c.get("/api/hub/accounts?role=main", headers=H).json()["items"][0]
    assert c.delete(f"/api/hub/accounts/{main['id']}", headers=H).status_code == 400   # the main account stays
    assert c.delete(f"/api/hub/accounts/{acc['id']}", headers=H).json()["ok"]
    assert any("removing-lukekicks" in b["name"] for b in c.get("/api/hub/backups", headers=H).json()["items"])
    assert c.delete(f"/api/hub/groups/{g['id']}", headers=H).json()["ok"]


def test_activity_integrations_and_manual_backup(app):
    a, c, tmp_path = app
    load(a, tmp_path)
    a.store.log("warn", "something to look at")
    assert c.get("/api/hub/activity?kind=warn", headers=H).json()["items"][0]["text"] == "something to look at"
    items = {i["id"]: i for i in c.get("/api/hub/integrations", headers=H).json()["items"]}
    assert items["anthropic"]["status"] == "not_configured" and items["instagram_download"]["status"] == "connected"
    assert items["youtube_api"]["status"] == "not_configured" and items["youtube_api"]["connectable"] and items["youtube_api"]["can"]
    assert items["tiktok_api"]["status"] == "not_configured" and items["instagram_api"]["connectable"]
    assert c.post("/api/hub/backups", headers=H).json()["name"].endswith("manual.db")
    assert c.get("/api/hub/overview").status_code == 401                           # every new endpoint needs the token


def test_the_command_centre_and_the_classic_screens_are_both_served(app):
    a, c, _ = app
    assert "Convo" in c.get("/").text
    assert "approveAll" in c.get("/classic").text
