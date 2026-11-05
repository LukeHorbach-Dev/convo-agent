"""Stage 6: missions from genuine activity, honest goals, an independence score only from measured parts, launch
mode, collaborations, and comment replies that are only posted when Luke presses Send."""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant.app import Assistant, create_api
from assistant.config import AssistantSettings
from assistant.growth import BUILT_IN, independence, missions
from assistant.practice import PracticeLLM
from assistant.store import Store

H = {"X-Agent-Token": "t"}
NOW = datetime.now(timezone.utc)
ms = lambda d: int(d.timestamp() * 1000)
iso = lambda d: d.isoformat(timespec="seconds")


@pytest.fixture
def g(tmp_path):
    shutil.copy(Path(__file__).parent / "fixtures" / "profile.json", tmp_path / "profile.json")
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "profile.json", never_list_path=tmp_path / "n.txt")
    a = Assistant(s, store=Store(tmp_path / "a.db"), make_web=lambda: None, llm=PracticeLLM(), notify=lambda *x: None,
                  practice=object(), env_path=tmp_path / ".env")
    return a, TestClient(create_api(a, "t"), base_url="http://127.0.0.1")


def chat(st, tid, back_and_forth=True, when=NOW - timedelta(hours=2)):
    st.execute("INSERT INTO threads(id, username, other_name, is_group, is_request, last_at, last_from_me, message_count) VALUES (?, ?, ?, 0, 0, ?, 1, 2)",
               (tid, tid, tid, ms(when)))
    st.execute("INSERT INTO messages(thread_id, at, from_me, sender, text) VALUES (?, ?, 0, ?, 'hey')", (tid, ms(when) - 1000, tid))
    if back_and_forth:
        st.execute("INSERT INTO messages(thread_id, at, from_me, sender, text) VALUES (?, ?, 1, 'me', 'hi!')", (tid, ms(when)))


def test_missions_count_real_activity_and_never_reward_sending_volume(g):
    a, c = g
    assert not any("hello" in m[2] and "sent" in m[2] for m in BUILT_IN)                 # no "send N hellos" mission
    for i in range(5):
        chat(a.store, f"friend{i}")
    chat(a.store, "oneway", back_and_forth=False)
    m = c.get("/api/hub/missions", headers=H).json()
    conv = next(x for x in m["missions"] if x["key"] == "conversations")
    assert conv["value"] == 5 and conv["done"] and m["xp_week"] >= 50
    assert a.store.scalar("SELECT COUNT(*) FROM mission_weeks") == len(BUILT_IN)          # saved into the history
    # A custom mission, measured the same way.
    assert c.post("/api/hub/missions", json={"title": "Save ideas", "metric": "ideas_saved", "target": 1}, headers=H).status_code == 200
    c.post("/api/hub/content", json={"title": "x"}, headers=H)
    m = c.get("/api/hub/missions", headers=H).json()
    assert next(x for x in m["missions"] if x["custom"])["done"]
    assert c.post("/api/hub/missions", json={"title": "x", "metric": "messages_blasted", "target": 500}, headers=H).status_code == 400


def test_goals_are_judged_only_with_real_numbers(g):
    a, c = g
    aid = a.store.execute("INSERT INTO accounts(platform, handle, role, created_at, updated_at) VALUES ('tiktok', 'luke.kicks', 'launch', 'x', 'x')").lastrowid
    r = c.post("/api/hub/goals", json={"title": "1k on TikTok", "metric": "followers", "account_id": aid, "target": 1000, "due": "2027-03-01"}, headers=H).json()
    assert r["status"] == "no_data"                                                        # no numbers for it yet
    assert c.post(f"/api/hub/accounts/{aid}/numbers", json={"followers": 1200}, headers=H).status_code == 200
    item = c.get("/api/hub/goals", headers=H).json()["items"][0]
    assert item["current"] == 1200 and item["status"] == "reached" and item["done_at"]
    assert c.post("/api/hub/goals", json={"title": "x", "metric": "vibes", "target": 1}, headers=H).status_code == 400


def test_the_independence_score_needs_two_measured_parts(g):
    a, c = g
    r = independence(a.store)
    assert r["score"] is None and r["measured"] == 0 and "Not enough" in r["advice"]
    a.store.set("youtube_traffic", {"sources": [{"source": "YT_SEARCH", "views": 300}, {"source": "EXT_URL", "views": 100}]})
    assert independence(a.store)["score"] is None                                         # one part isn't enough
    for i in range(6):
        chat(a.store, f"p{i}", back_and_forth=i < 3, when=NOW - timedelta(days=2))
    r = c.get("/api/hub/independence", headers=H).json()
    parts = {p["id"]: p["score"] for p in r["parts"]}
    assert parts["discovery"] == 75 and parts["conversations"] == 50 and r["score"] == 62 and r["advice"].startswith("Suggestion")


def test_launch_mode_checklist_and_shout_out(g):
    a, c = g
    main = a.store.scalar("SELECT id FROM accounts WHERE role = 'main'")
    aid = c.post("/api/hub/accounts", json={"platform": "tiktok", "handle": "luke.kicks", "role": "launch", "niche": "Football",
                                            "purpose": "Clips", "referral_link": "https://lnk.bio/luke"}, headers=H).json()["id"]
    item = c.get("/api/hub/launch", headers=H).json()["items"][0]
    assert item["done"] == 2 and not next(s for s in item["steps"] if s["id"] == "promo")["done"]
    cid = c.post(f"/api/hub/launch/{aid}/shoutout", headers=H).json()["content_id"]
    post = c.get(f"/api/hub/content/{cid}", headers=H).json()
    assert post["account_id"] == main and "@luke.kicks" in post["title"] and post["status"] == "idea"
    assert next(s for s in c.get("/api/hub/launch", headers=H).json()["items"][0]["steps"] if s["id"] == "promo")["done"]


def test_collaborations(g):
    a, c = g
    r = c.post("/api/hub/collabs", json={"name": "Maya", "username": "@Maya.Paints", "idea": "Paint and bake video"}, headers=H).json()
    assert r["username"] == "maya.paints" and r["stage"] == "idea"
    assert c.patch(f"/api/hub/collabs/{r['id']}", json={"stage": "talking"}, headers=H).json()["stage"] == "talking"
    assert c.patch(f"/api/hub/collabs/{r['id']}", json={"stage": "married"}, headers=H).status_code == 400


def test_comment_replies_are_only_posted_when_sent(g, tmp_path):
    a, c = g
    from assistant.connect import Connections
    posted = []

    def ig(req: httpx.Request):
        if req.url.path.endswith("/replies"):
            posted.append(req.content.decode())
            return httpx.Response(200, json={"id": "r1"})
        return httpx.Response(404, json={})
    a.connect = Connections(tmp_path / "integrations", 8765, log=a.store.log, http=httpx.Client(transport=httpx.MockTransport(ig)))
    a.connect.all["instagram"].vault.write({"client_id": "x" * 10, "client_secret": "y" * 10,
                                            "tokens": {"access_token": "tok", "expires_at": 9e12}})
    a.store.execute("INSERT INTO comments(platform, comment_id, post_id, username, text, at) VALUES ('instagram', 'c1', 'm1', 'fan', 'love this!', ?)", (iso(NOW),))
    a.store.execute("INSERT INTO comments(platform, comment_id, post_id, username, text, at) VALUES ('youtube', 'y1', 'v1', 'viewer', 'nice', ?)", (iso(NOW),))
    w = c.post("/api/hub/comments/instagram/c1/write", headers=H).json()
    assert w["reply_status"] == "draft" and w["reply_text"] and posted == []              # written, not posted
    assert c.post("/api/hub/comments/instagram/c1/send", json={"text": "send me your address"}, headers=H).status_code == 400
    s = c.post("/api/hub/comments/instagram/c1/send", json={"text": "thank you!"}, headers=H).json()
    assert s["reply_status"] == "sent" and posted == ["message=thank+you%21"]
    assert c.post("/api/hub/comments/instagram/c1/send", json={"text": "again"}, headers=H).status_code == 409
    assert c.post("/api/hub/comments/youtube/y1/send", json={"text": "thanks"}, headers=H).status_code == 400
    assert c.post("/api/hub/comments/youtube/y1/skip", headers=H).json()["reply_status"] == "skipped"
