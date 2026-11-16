"""One button: everyone you follow goes on the unfollow list and Convo starts unfollowing, and only unfollowing
(approved replies wait for Send)."""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from assistant.app import Assistant, create_api
from assistant.config import AssistantSettings
from assistant.practice import PracticeLLM
from assistant.store import Store

H = {"X-Agent-Token": "t"}


def test_one_button_unfollows_everyone_and_nothing_else(tmp_path):
    shutil.copy(Path(__file__).parent / "fixtures" / "profile.json", tmp_path / "profile.json")
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "profile.json", never_list_path=tmp_path / "n.txt")
    a = Assistant(s, store=Store(tmp_path / "a.db"), make_web=lambda: None, llm=PracticeLLM(), notify=lambda *x: None, env_path=tmp_path / ".env")
    st = a.store
    for u, f in (("ann", 1), ("ben", 1), ("cal", 0)):
        st.execute("INSERT INTO people(username, i_follow) VALUES (?, ?)", (u, f))
    st.execute("INSERT INTO drafts(kind, username, text, status, created_at, updated_at) VALUES ('hello', 'ann', 'hey', 'draft', 'x', 'x')")
    st.execute("INSERT INTO drafts(kind, username, thread_id, text, status, created_at, updated_at) VALUES ('reply', 'cal', 't', 'see you', 'approved', 'x', 'x')")
    a.sender.stop_now()                                             # as on Luke's screen: "Stopped by you"
    c = TestClient(create_api(a, "t"), base_url="http://127.0.0.1")
    r = c.post("/api/unfollow/everyone/start", headers=H).json()
    assert r["queued"] == 2 and r["waiting"] == 2 and r["per_day"] == 60 and r["days"] == 1
    assert {x["username"] for x in st.all("SELECT username FROM unfollows WHERE status = 'queued'")} == {"ann", "ben"}
    assert st.scalar("SELECT status FROM drafts WHERE kind = 'hello'") == "skipped"
    assert a.sender.active and a.sender.unfollow_only and not a.sender.halt.is_set()
    # The sender picks an unfollow, not the approved reply.
    picked = []
    a.sender._ready = lambda: True
    a.sender.unfollow_one = lambda u: picked.append(("unfollow", u["username"]))
    a.sender.send_one = lambda m: picked.append(("send", m["username"]))
    a.sender._sleep = lambda s: None
    a.sender.step()
    assert picked == [("unfollow", "ann")]
    # Stop now clears the mode; pressing the button again with nobody left says so.
    a.sender.stop_now()
    assert not a.sender.unfollow_only
    st.execute("UPDATE unfollows SET status = 'done'")
    assert c.post("/api/unfollow/everyone/start", headers=H).status_code == 400
    assert st.scalar("SELECT status FROM drafts WHERE kind = 'reply'") == "approved"         # the reply was left alone
