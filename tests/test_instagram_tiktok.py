"""Instagram and TikTok: sign in through the https return page (state names the platform and port), keys kept
owner-only, numbers synced, Reels published only when asked, TikTok videos sent to the inbox as drafts."""
from __future__ import annotations

import json
import shutil
import time
import urllib.parse
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant.app import Assistant, create_api
from assistant.config import AssistantSettings
from assistant.connect import Connections
from assistant.connect.base import RELAY
from assistant.practice import PracticeLLM
from assistant.store import Store

H = {"X-Agent-Token": "t"}


class FakeMeta:
    def __init__(self):
        self.calls, self.published, self.reel_bytes = [], [], b""
        self.tiktok_chunks = []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        u = req.url
        self.calls.append((req.method, u.host, u.path))
        # Instagram
        if u.host == "api.instagram.com" and u.path == "/oauth/access_token":
            return httpx.Response(200, json={"access_token": "short", "user_id": 17})
        if u.host == "graph.instagram.com" and u.path == "/access_token":
            assert u.params["access_token"] == "short"
            return httpx.Response(200, json={"access_token": "long-ig", "expires_in": 5184000})
        if u.host == "graph.instagram.com":
            assert u.params.get("access_token") == "long-ig" or req.headers.get("authorization", "").endswith("long-ig")
            p = u.path
            if p.endswith("/me") :
                return httpx.Response(200, json={"user_id": "17", "username": "lukehorbach2", "name": "Luke", "account_type": "MEDIA_CREATOR",
                                                 "followers_count": 812, "follows_count": 1765, "media_count": 40})
            if p.endswith("/me/media") and req.method == "GET":
                return httpx.Response(200, json={"data": [{"id": "m1", "caption": "bake day", "media_type": "IMAGE", "permalink": "https://www.instagram.com/p/m1/",
                                                           "timestamp": "2026-11-01T10:00:00+0000", "media_url": "https://scontent.cdninstagram.com/1.jpg",
                                                           "like_count": 40, "comments_count": 3}]})
            if p.endswith("/m1/insights"):
                return httpx.Response(200, json={"data": [{"name": "views", "values": [{"value": 1200}]}, {"name": "reach", "values": [{"value": 900}]},
                                                          {"name": "saved", "values": [{"value": 12}]}, {"name": "shares", "values": [{"value": 5}]}]})
            if p.endswith("/me/insights"):
                metric = u.params["metric"]
                if metric == "follower_count":
                    return httpx.Response(400, json={"error": {"message": "needs 100 followers"}})
                return httpx.Response(200, json={"data": [{"name": "reach", "values": [{"value": 300, "end_time": "2026-11-01T08:00:00+0000"},
                                                                                       {"value": 450, "end_time": "2026-11-02T08:00:00+0000"}]}]})
            if p.endswith("/me/media") and req.method == "POST":
                form = dict(urllib.parse.parse_qsl(req.content.decode()))
                assert form["media_type"] == "REELS" and form["upload_type"] == "resumable"
                self.caption = form["caption"]
                return httpx.Response(200, json={"id": "c1", "uri": "https://rupload.facebook.com/ig-api-upload/v23.0/c1"})
            if p.endswith("/c1"):
                return httpx.Response(200, json={"status_code": "FINISHED"})
            if p.endswith("/me/media_publish"):
                self.published.append(dict(urllib.parse.parse_qsl(req.content.decode())))
                return httpx.Response(200, json={"id": "reel9"})
            if p.endswith("/reel9"):
                return httpx.Response(200, json={"permalink": "https://www.instagram.com/reel/reel9/"})
        if u.host == "rupload.facebook.com":
            assert req.headers["authorization"] == "OAuth long-ig"
            self.reel_bytes = req.content
            return httpx.Response(200, json={"success": True})
        # TikTok
        if u.host == "open.tiktokapis.com":
            if u.path == "/v2/oauth/token/":
                form = dict(urllib.parse.parse_qsl(req.content.decode()))
                assert form["client_key"] and form["code_verifier"]
                return httpx.Response(200, json={"access_token": "tt-at", "refresh_token": "tt-rt", "expires_in": 86400, "open_id": "o1"})
            assert req.headers["authorization"] == "Bearer tt-at"
            if u.path == "/v2/user/info/":
                return httpx.Response(200, json={"data": {"user": {"open_id": "o1", "display_name": "Luke", "username": "luke.kicks",
                    "follower_count": 230, "following_count": 12, "likes_count": 4100, "video_count": 9}}, "error": {"code": "ok"}})
            if u.path == "/v2/video/list/":
                return httpx.Response(200, json={"data": {"videos": [{"id": "t1", "title": "nutmeg", "create_time": 1762000000, "share_url": "https://www.tiktok.com/@luke.kicks/video/t1",
                    "view_count": 5000, "like_count": 300, "comment_count": 20, "share_count": 9}]}, "error": {"code": "ok"}})
            if u.path == "/v2/post/publish/inbox/video/init/":
                info = json.loads(req.content)["source_info"]
                assert info["source"] == "FILE_UPLOAD" and info["total_chunk_count"] == 1
                return httpx.Response(200, json={"data": {"publish_id": "pub1", "upload_url": "https://open-upload.tiktokapis.com/upload/?id=1"}, "error": {"code": "ok"}})
        if u.host == "open-upload.tiktokapis.com":
            self.tiktok_chunks.append((req.headers["content-range"], len(req.content)))
            return httpx.Response(201)
        return httpx.Response(404, json={"error": {"message": f"unexpected {u}"}})


@pytest.fixture
def env(tmp_path):
    shutil.copy(Path(__file__).parent / "fixtures" / "profile.json", tmp_path / "profile.json")
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "profile.json", never_list_path=tmp_path / "n.txt")
    a = Assistant(s, store=Store(tmp_path / "a.db"), make_web=lambda: None, llm=PracticeLLM(), notify=lambda *x: None, env_path=tmp_path / ".env")
    fake = FakeMeta()
    a.connect = Connections(tmp_path / "integrations", 8765, log=a.store.log, http=httpx.Client(transport=httpx.MockTransport(fake)))
    return a, TestClient(create_api(a, "t"), base_url="http://127.0.0.1"), fake, tmp_path


def connect(c, pid):
    c.post(f"/api/connect/{pid}/app", json={"client_id": "1234567890", "client_secret": "abcdef123456"}, headers=H)
    url = c.post(f"/api/connect/{pid}/start", headers=H).json()["url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    return url, q


def wait_done(c, cid, pid):
    for _ in range(100):
        s = c.get(f"/api/hub/content/{cid}/publish/{pid}", headers=H).json()
        if s["done"]:
            return s
        time.sleep(0.05)
    raise AssertionError("never finished")


def test_instagram_connects_syncs_and_publishes_a_reel_only_when_asked(env):
    a, c, fake, tmp = env
    url, q = connect(c, "instagram")
    assert url.startswith("https://www.instagram.com/oauth/authorize") and q["redirect_uri"] == RELAY
    assert q["state"].startswith("instagram.8765.") and "instagram_business_content_publish" in q["scope"].split(",")
    assert "is connected" in c.get(f"/oauth/instagram?code=abc&state={q['state']}").text
    keys = json.loads((tmp / "integrations" / "instagram.json").read_text())
    assert keys["tokens"]["access_token"] == "long-ig" and keys["tokens"]["expires_at"] > time.time() + 50 * 86400
    out = c.post("/api/connect/instagram/sync", headers=H).json()
    out.pop("new_comments", None)
    assert out == {"handle": "lukehorbach2", "followers": 812, "posts": 1, "days": 2}
    ov = c.get("/api/connect/instagram/overview", headers=H).json()
    assert ov["followers"] == 812 and ov["recent"][0]["views"] == 1200 and ov["days"][1]["reach"] == 450
    # The main Instagram account gets the API connection added (downloads keep working).
    main = a.store.one("SELECT connection, status FROM accounts WHERE platform = 'instagram' AND handle = 'lukehorbach2'")
    assert "api" in main["connection"] and main["status"] == "connected"
    # Photos can't be published (Instagram needs a public address for them); Reels can.
    cid = c.post("/api/hub/content", json={"title": "Bake", "kind": "photo", "platforms": ["instagram"]}, headers=H).json()["id"]
    c.post(f"/api/hub/content/{cid}/upload?filename=cake.jpg", content=b"\xff\xd8" + b"0" * 100, headers=H)
    r = c.post(f"/api/hub/content/{cid}/publish/instagram", headers=H)
    assert r.status_code == 400 and "copy the caption" in r.json()["detail"]
    assert fake.published == []
    rid = c.post("/api/hub/content", json={"title": "Derby", "kind": "short", "platforms": ["instagram"],
                                           "versions": {"instagram": {"caption": "derby day", "hashtags": ["football"]}}}, headers=H).json()["id"]
    c.post(f"/api/hub/content/{rid}/upload?filename=clip.mp4", content=b"\x00" * 500, headers=H)
    import assistant.connect.instagram as ig
    ig_publish = ig.publish_reel
    ig.publish_reel = lambda *x, **k: ig_publish(*x, **{**k, "wait_s": 0})
    try:
        assert c.post(f"/api/hub/content/{rid}/publish/instagram", headers=H).status_code == 200
        s = wait_done(c, rid, "instagram")
    finally:
        ig.publish_reel = ig_publish
    assert s["error"] is None and s["result"] == "https://www.instagram.com/reel/reel9/"
    assert fake.reel_bytes == b"\x00" * 500 and fake.caption == "derby day\n\n#football" and fake.published == [{"creation_id": "c1"}]
    item = c.get(f"/api/hub/content/{rid}", headers=H).json()
    assert item["status"] == "posted" and item["post_url"] == "https://www.instagram.com/reel/reel9/"


def test_tiktok_connects_syncs_and_sends_a_draft(env):
    a, c, fake, tmp = env
    url, q = connect(c, "tiktok")
    assert url.startswith("https://www.tiktok.com/v2/auth/authorize/") and q["client_key"] == "1234567890" and "client_id" not in q
    assert q["state"].startswith("tiktok.8765.") and q["code_challenge_method"] == "S256"
    assert "is connected" in c.get(f"/oauth/tiktok?code=abc&state={q['state']}").text
    out = c.post("/api/connect/tiktok/sync", headers=H).json()
    assert out == {"handle": "luke.kicks", "followers": 230, "videos": 1}
    ov = c.get("/api/connect/tiktok/overview", headers=H).json()
    assert ov["followers"] == 230 and ov["likes_total"] == 4100 and ov["recent"][0]["views"] == 5000
    cid = c.post("/api/hub/content", json={"title": "Nutmeg", "kind": "short", "platforms": ["tiktok"]}, headers=H).json()["id"]
    c.post(f"/api/hub/content/{cid}/upload?filename=clip.mov", content=b"\x01" * 700, headers=H)
    assert c.post(f"/api/hub/content/{cid}/publish/tiktok", headers=H).status_code == 200
    s = wait_done(c, cid, "tiktok")
    assert s["error"] is None and s["result"] == "pub1" and fake.tiktok_chunks == [("bytes 0-699/700", 700)]
    assert c.get(f"/api/hub/content/{cid}", headers=H).json()["status"] != "posted"          # a draft: Luke posts it


def test_the_relay_page_only_forwards_convo_sign_ins():
    page = (Path(__file__).parent.parent / "docs" / "oauth.html").read_text()
    assert "^(instagram|tiktok)\\.(\\d{2,5})\\." in page and "http://127.0.0.1:" in page
    assert "fetch(" not in page and "XMLHttpRequest" not in page                              # it sends nothing anywhere else
