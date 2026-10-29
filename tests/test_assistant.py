"""The Instagram assistant (v2): reading the download, hellos, writing replies, sending through the mock site,
unfollowing, and the dashboard API."""
from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agent.core.errors import (AuthenticationRequired, PermissionRestricted, PlatformUnavailable, RateLimited,
                               SelectorDrift)
from agent.intel.llm import FakeLLM
from assistant.app import Assistant, create_api
from assistant.brain import chats_waiting, prepare_hellos, think
from assistant.config import AssistantSettings
from assistant.export import ExportError, apply_export, find_downloads, fix_text, read_export
from assistant.sender import classify
from assistant.store import Store

ME = "LukeHorbach2"
DAY_MS = 86_400_000
NOW_MS = int(time.time() * 1000)


def mojibake(s: str) -> str:
    """How Instagram writes non-ASCII text in its download."""
    return s.encode("utf-8").decode("latin-1")


def chat(participants, messages, title=""):
    return {"participants": [{"name": mojibake(p)} for p in participants], "title": mojibake(title or participants[0]),
            "messages": [{"sender_name": mojibake(s), "timestamp_ms": t, "content": mojibake(c)} if c is not None
                         else {"sender_name": mojibake(s), "timestamp_ms": t, "photos": [{"uri": "x.jpg"}]}
                         for s, t, c in reversed(messages)],   # the download lists newest first
            "is_still_participant": True}


def make_export(folder: Path, name="instagram-lukehorbach2-2026-10-29-abc.zip") -> Path:
    files = {
        "personal_information/personal_information/personal_information.json": {
            "profile_user": [{"string_map_data": {"Username": {"value": "lukehorbach2"}, "Name": {"value": ME}}}]},
        # newer layout: username in title, href with _u/
        "connections/followers_and_following/following.json": {"relationships_following": [
            {"title": "anna.walsh", "string_list_data": [{"href": "https://www.instagram.com/_u/anna.walsh", "timestamp": 1700000300}]},
            {"title": "", "string_list_data": [{"href": "https://www.instagram.com/new.friend", "value": "new.friend", "timestamp": 1700000400}]},
            {"title": "kid_2010", "string_list_data": [{"href": "https://www.instagram.com/_u/kid_2010", "timestamp": 1700000500}]},
            {"title": "nope.person", "string_list_data": [{"href": "https://www.instagram.com/_u/nope.person", "timestamp": 1700000600}]},
            {"title": "mia.skis", "string_list_data": [{"href": "https://www.instagram.com/_u/mia.skis", "timestamp": 1700000100}]},
        ]},
        # older layout: a plain list with value
        "connections/followers_and_following/followers_1.json": [
            {"title": "", "string_list_data": [{"href": "https://www.instagram.com/anna.walsh", "value": "anna.walsh", "timestamp": 1}]},
            {"title": "", "string_list_data": [{"href": "https://www.instagram.com/fan.only", "value": "fan.only", "timestamp": 2}]},
        ],
        "your_instagram_activity/messages/inbox/annawalsh_10224/message_1.json": chat(["Anna Walsh", ME], [
            ("Anna Walsh", NOW_MS - 3 * DAY_MS, "hey how are you"),
            (ME, NOW_MS - 2 * DAY_MS, "Not much just finishing up uni atm hby ?"),
            ("Anna Walsh", NOW_MS - DAY_MS, "I’m good, I paint a lot 🎨 what do you do for fun?")]),
        "your_instagram_activity/messages/inbox/miaskis_555/message_1.json": chat(["Mia", ME], [
            ("Mia", NOW_MS - 5 * DAY_MS, "hii"), (ME, NOW_MS - 4 * DAY_MS, "Hey, hows your week going ?")]),
        "your_instagram_activity/messages/inbox/somebodynew_777/message_1.json": chat(["Zoe Q", ME], [
            ("Zoe Q", NOW_MS - DAY_MS, None)]),                         # a photo, no text
        "your_instagram_activity/messages/inbox/youngone_888/message_1.json": chat(["Sam", ME], [
            ("Sam", NOW_MS - DAY_MS, "hey im 16 btw")]),
        "your_instagram_activity/messages/inbox/stopper_999/message_1.json": chat(["Pat", ME], [
            ("Pat", NOW_MS - DAY_MS, "please stop messaging me")]),
        "your_instagram_activity/messages/inbox/thecrew_123/message_1.json": chat(["Ann", "Bob", ME], [
            ("Ann", NOW_MS - DAY_MS, "group plans?")], title="The crew"),
        "your_instagram_activity/messages/message_requests/stranger_321/message_1.json": chat(["Stranger", ME], [
            ("Stranger", NOW_MS - DAY_MS, "hi")]),
    }
    path = folder / name
    with zipfile.ZipFile(path, "w") as z:
        for n, data in files.items():
            z.writestr(n, json.dumps(data))
    return path


@pytest.fixture
def export(tmp_path) -> Path:
    return make_export(tmp_path)


@pytest.fixture
def store():
    return Store(":memory:")


@pytest.fixture
def profile_path(tmp_path) -> Path:
    src = Path(__file__).parent / "fixtures" / "profile.json"
    p = tmp_path / "profile.json"
    p.write_text(src.read_text())
    return p


def load(store, export):
    return apply_export(store, read_export([export]), "lukehorbach2")


# ------------------------------------------------------------------------------------------------ the download
def test_fix_text_undoes_instagrams_escaping():
    assert fix_text(mojibake("I’m good 🎨 café")) == "I’m good 🎨 café"
    assert fix_text("already fine ✓") == "already fine ✓"


def test_download_is_read_in_both_layouts(export):
    d = read_export([export])
    assert d.me_username == "lukehorbach2" and d.me_name == ME
    assert set(d.following) == {"anna.walsh", "new.friend", "kid_2010", "nope.person", "mia.skis"}
    assert d.followers == {"anna.walsh", "fan.only"}
    anna = next(t for t in d.threads if t.id == "annawalsh_10224")
    assert [m.text for m in anna.messages][-1] == "I’m good, I paint a lot 🎨 what do you do for fun?"   # oldest first
    assert next(t for t in d.threads if t.id == "somebodynew_777").messages[0].text == "[photo]"
    assert next(t for t in d.threads if t.id == "stranger_321").is_request


def test_loading_maps_chats_to_usernames_and_is_safe_to_repeat(store, export):
    s1 = load(store, export)
    assert s1["following"] == 5 and s1["new_messages"] > 0
    assert store.one("SELECT username FROM threads WHERE id = 'annawalsh_10224'")["username"] == "anna.walsh"
    assert store.one("SELECT username FROM threads WHERE id = 'miaskis_555'")["username"] == "mia.skis"
    assert store.one("SELECT username FROM threads WHERE id = 'somebodynew_777'")["username"] is None   # not known
    assert store.one("SELECT is_group FROM threads WHERE id = 'thecrew_123'")["is_group"] == 1
    assert load(store, export)["new_messages"] == 0                     # the next day's download repeats history


def test_a_download_without_the_right_files_says_what_to_do(tmp_path):
    bad = tmp_path / "instagram-x.zip"
    with zipfile.ZipFile(bad, "w") as z:
        z.writestr("index.html", "<html></html>")
    with pytest.raises(ExportError, match="JSON"):
        read_export([bad])


def test_newest_download_and_its_parts_are_found(tmp_path):
    a = make_export(tmp_path, "instagram-lukehorbach2-2026-10-28-aaa.zip")
    time.sleep(0.01)
    b = make_export(tmp_path, "instagram-lukehorbach2-2026-10-29-bbb.zip")
    c = make_export(tmp_path, "instagram-lukehorbach2-2026-10-29-bbb-part2.zip")
    assert set(find_downloads(tmp_path)) == {b, c} and a not in find_downloads(tmp_path)


# ------------------------------------------------------------------------------------------------ hellos
def test_hellos_go_to_people_you_follow_and_never_messaged(store, export, profile_path):
    from agent.intel.profile import Profile
    load(store, export)
    made = prepare_hellos(store, Profile.load(profile_path), never={"nope.person"})
    names = {r["username"] for r in store.all("SELECT username FROM drafts WHERE kind = 'hello'")}
    # anna and mia have chats; kid_2010 looks under 19; nope.person is on the never list; fan.only you don't follow
    assert names == {"new.friend"} and made == 1
    assert prepare_hellos(store, Profile.load(profile_path), never=set()) == 1    # only nope.person is new now
    assert store.scalar("SELECT COUNT(*) FROM drafts WHERE kind = 'hello' AND username = 'new.friend'") == 1


# ------------------------------------------------------------------------------------------------ replies
def answer(reply, **kw):
    base = {"reply": reply, "needs_human": False, "reason": "", "summary": "Anna, a painter", "interest": 3,
            "interest_why": "art, new things", "concern": "none", "concern_why": "", "facts_used": [],
            "memory_notes": ["paints a lot"], "language": "English", "their_message_english": "x", "reply_english": "x"}
    return {**base, **kw}


def test_replies_are_written_for_chats_waiting_on_you_with_hard_rules_first(store, export, profile_path):
    from agent.intel.profile import Profile
    load(store, export)
    waiting = {t["id"] for t in chats_waiting(store, 90)}
    # mia: you wrote last; the crew: group; stranger: a request
    assert waiting == {"annawalsh_10224", "somebodynew_777", "youngone_888", "stopper_999"}
    llm = FakeLLM()
    llm.queue.extend([answer("Ahh that's cool! I've just started pottery, what do you paint?"), answer("Nice photo")])
    out = think(store, Profile.load(profile_path), llm, never=set())
    by = {d["thread_id"]: d for d in store.all("SELECT * FROM drafts WHERE kind = 'reply'")}
    assert by["youngone_888"]["status"] == "skipped" and "16" in by["youngone_888"]["note"]
    assert by["stopper_999"]["status"] == "skipped"
    anna = by["annawalsh_10224"]
    assert anna["text"] == "Ahh thats cool. ive just started pottery, what do you paint ?"        # Luke's punctuation
    assert json.loads(anna["context"])["interest"] == 3
    assert store.one("SELECT interest, memory FROM people WHERE username = 'anna.walsh'")["interest"] == 3
    assert "paints a lot" in store.one("SELECT memory FROM people WHERE username = 'anna.walsh'")["memory"]
    assert out["drafted"] == 2 and out["skipped"] == 2
    assert think(store, Profile.load(profile_path), llm, never=set())["chats"] == 0     # nothing twice


def test_without_ai_the_boxes_are_left_for_luke(store, export, profile_path):
    from agent.intel.profile import Profile
    load(store, export)
    think(store, Profile.load(profile_path), FakeLLM(), never=set(), manual=True)
    d = store.one("SELECT * FROM drafts WHERE thread_id = 'annawalsh_10224'")
    assert d["text"] == "" and json.loads(d["context"])["manual"] is True


def test_concerning_chats_are_flagged_and_not_replied(store, export, profile_path):
    from agent.intel.profile import Profile
    load(store, export)
    llm = FakeLLM()
    llm.queue.extend([answer("", concern="high", concern_why="asked for money", needs_human=True, reason="money"), answer("nice")])
    think(store, Profile.load(profile_path), llm, never=set())
    ctx = json.loads(store.one("SELECT context FROM drafts WHERE thread_id = 'annawalsh_10224'")["context"])
    assert ctx["concern"] == "high" and ctx["needs_look"] is True


# ------------------------------------------------------------------------------------------------ problems
def test_problems_are_sorted_into_what_to_do():
    assert classify(AuthenticationRequired("x"))[0] == "stop"
    assert classify(RateLimited("x"))[0] == "cooldown"
    assert classify(PermissionRestricted("u: private"))[0] == "skip"
    assert classify(SelectorDrift("x"))[0] == "page"
    assert classify(PlatformUnavailable("x"))[0] == "later"


# Sending and the self-check are tested in test_eyes.py (version 3's website layer).
def settings_for(tmp_path) -> AssistantSettings:
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "profile.json",
                          never_list_path=tmp_path / "never.txt", downloads_dir=tmp_path)
    s.pace.message_gap_s = s.pace.unfollow_gap_s = 0
    return s


# ------------------------------------------------------------------------------------------------ dashboard
@pytest.fixture
def client(tmp_path, profile_path, export):
    s = settings_for(tmp_path)
    (tmp_path / "never.txt").write_text("nope.person\n")
    a = Assistant(s, store=Store(":memory:"), make_web=lambda: None, llm=FakeLLM(), notify=lambda *a: None)
    return a, TestClient(create_api(a, "tok"), base_url="http://127.0.0.1")


H = {"X-Agent-Token": "tok"}


def wait_job(a):
    for _ in range(200):
        if a.job["name"] is None:
            return
        time.sleep(0.02)


def test_dashboard_needs_the_token_and_a_local_host(client):
    _, c = client
    assert c.get("/api/state").status_code == 401
    assert TestClient(c.app, base_url="http://evil.example").get("/api/state", headers=H).status_code == 403


def test_dashboard_load_check_approve_flow(client, monkeypatch):
    a, c = client
    monkeypatch.chdir(a.s.data_dir)
    assert c.post("/api/import", headers=H, json={}).status_code == 200          # finds the zip in "Downloads"
    wait_job(a)
    s = c.get("/api/state", headers=H).json()
    assert s["last_import"]["following"] == 5 and s["counts"]["hellos_to_check"] == 1
    assert s["counts"]["replies_to_check"] == 2                                   # manual mode: empty boxes
    r = c.get("/api/drafts?kind=reply&status=draft", headers=H).json()
    anna = next(d for d in r["items"] if d["thread_id"] == "annawalsh_10224")
    assert anna["recent"][-1]["text"].startswith("I’m good")
    assert c.post(f"/api/drafts/{anna['id']}", headers=H, json={"action": "approve", "text": ""}).status_code == 400
    assert c.post(f"/api/drafts/{anna['id']}", headers=H, json={"action": "approve", "text": "where do you live?"}).status_code == 400
    assert c.post(f"/api/drafts/{anna['id']}", headers=H, json={"action": "approve", "text": "Ahh nice, what do you paint ?"}).status_code == 200
    assert c.post("/api/drafts/all/hello", headers=H).json()["approved"] == 1
    assert c.get("/api/state", headers=H).json()["counts"]["approved"] == 2


def test_dashboard_unfollow_everyone_needs_the_words(client, monkeypatch):
    a, c = client
    monkeypatch.chdir(a.s.data_dir)
    c.post("/api/import", headers=H, json={}); wait_job(a)
    assert c.post("/api/unfollow", headers=H, json={"everyone": True, "confirm": "yes"}).status_code == 400
    assert c.post("/api/unfollow", headers=H, json={"everyone": True, "confirm": "UNFOLLOW EVERYONE"}).json()["queued"] == 5
    assert a.store.scalar("SELECT status FROM drafts WHERE kind = 'hello'") == "skipped"


def test_about_me_is_editable_and_private_facts_stay_private(client):
    a, c = client
    about = c.get("/api/about", headers=H).json()
    facts = about["facts"] + [{"text": "I support Chelsea and Barcelona", "private": False},
                              {"text": "a private thing", "private": True}]
    assert c.post("/api/about", headers=H, json={"facts": facts, "hellos": ["Hey im Luke :)", ""], "voice": "short",
                                                 "never": "someone\n"}).status_code == 200
    from agent.intel.profile import Profile
    p = Profile.load(a.s.profile_path)
    assert any(f.text.startswith("I support Chelsea") for f in p.verified_facts())
    assert not any(f.text == "a private thing" for f in p.verified_facts())
    assert p.intro_templates == ["Hey im Luke :)"] and a.s.never_list_path.read_text() == "someone\n"


def test_settings_save_the_ai_key_locally(client, monkeypatch):
    a, c = client
    monkeypatch.chdir(a.s.data_dir)
    assert c.post("/api/settings", headers=H, json={"ai_key": "nope"}).status_code == 400
    key = "sk-ant-api03-" + "x" * 40
    assert c.post("/api/settings", headers=H, json={"ai_key": key, "daily_messages": 80}).status_code == 200
    assert f"AGENT_LLM__API_KEY={key}" in (a.s.data_dir / ".env").read_text()
    s = c.get("/api/state", headers=H).json()
    assert s["ai"]["on"] is True and s["settings"]["daily_messages"] == 80


def test_username_from_profile_links():
    from agent.platform.web import username_from_href
    assert username_from_href("/thyuki.okada/") == "thyuki.okada"
    assert username_from_href("https://www.instagram.com/thyuki.okada/?hl=en") == "thyuki.okada"
    assert username_from_href("/direct/t/115229139868688/") is None
    assert username_from_href("/explore/") is None
