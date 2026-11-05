"""YouTube: sign in with Luke's own Google app (PKCE, state checked), keys kept owner-only, numbers synced into
Convo, and uploads go up as private. Google is pretended here; nothing leaves this machine."""
from __future__ import annotations

import json
import shutil
import urllib.parse
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from assistant.app import Assistant, create_api
from assistant.config import AssistantSettings
from assistant.connect import Connections
from assistant.practice import PracticeLLM
from assistant.store import Store

H = {"X-Agent-Token": "t"}


class FakeGoogle:
    def __init__(self):
        self.calls, self.uploaded, self.token_forms = [], b"", []

    def __call__(self, req: httpx.Request) -> httpx.Response:
        u = req.url
        self.calls.append((req.method, u.host, u.path))
        if u.host == "oauth2.googleapis.com":
            form = dict(urllib.parse.parse_qsl(req.content.decode()))
            self.token_forms.append(form)
            if form.get("code") == "bad":
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "Bad code"})
            return httpx.Response(200, json={"access_token": "at-1", "refresh_token": "rt-1", "expires_in": 3600, "scope": "x"})
        assert req.headers["authorization"] == "Bearer at-1"
        if u.path.endswith("/channels"):
            return httpx.Response(200, json={"items": [{"id": "UC1", "snippet": {"title": "Luke Horbach", "customUrl": "@lukehorbach"},
                "statistics": {"subscriberCount": "1234", "viewCount": "56789", "videoCount": "2"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
        if u.path.endswith("/playlistItems"):
            return httpx.Response(200, json={"items": [{"contentDetails": {"videoId": "v1"}}, {"contentDetails": {"videoId": "v2"}}]})
        if u.path.endswith("/videos") and u.host == "www.googleapis.com" and "/upload/" not in u.path:
            return httpx.Response(200, json={"items": [
                {"id": "v1", "snippet": {"title": "Derby day", "publishedAt": "2026-11-01T10:00:00Z", "thumbnails": {"medium": {"url": "https://i.ytimg.com/1.jpg"}}},
                 "statistics": {"viewCount": "900", "likeCount": "50", "commentCount": "7"}, "contentDetails": {"duration": "PT1M"}, "status": {"privacyStatus": "public"}},
                {"id": "v2", "snippet": {"title": "Exchange week", "publishedAt": "2026-10-20T10:00:00Z"}, "statistics": {"viewCount": "300"}}]})
        if u.host == "youtubeanalytics.googleapis.com":
            if req.url.params.get("dimensions") == "day":
                return httpx.Response(200, json={"columnHeaders": [{"name": n} for n in ("day", "views", "estimatedMinutesWatched", "averageViewDuration",
                                                                                          "subscribersGained", "subscribersLost")],
                                                 "rows": [["2026-11-01", 120, 300, 45, 6, 1], ["2026-11-02", 80, 200, 40, 2, 0]]})
            return httpx.Response(200, json={"rows": [["YT_SEARCH", 150], ["RELATED_VIDEO", 50]]})
        if "/upload/" in u.path and req.method == "POST":
            body = json.loads(req.content)
            assert body["status"]["privacyStatus"] == "private"
            self.meta = body
            return httpx.Response(200, headers={"location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=abc"})
        if "/upload/" in u.path and req.method == "PUT":
            self.uploaded += req.content
            return httpx.Response(200, json={"id": "newvid"})
        return httpx.Response(404, json={"error": {"message": "unknown"}})


@pytest.fixture
def yt(tmp_path):
    shutil.copy(Path(__file__).parent / "fixtures" / "profile.json", tmp_path / "profile.json")
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "profile.json", never_list_path=tmp_path / "n.txt")
    a = Assistant(s, store=Store(tmp_path / "a.db"), make_web=lambda: None, llm=PracticeLLM(), notify=lambda *x: None, env_path=tmp_path / ".env")
    g = FakeGoogle()
    a.connect = Connections(tmp_path / "integrations", 8765, log=a.store.log, http=httpx.Client(transport=httpx.MockTransport(g)))
    c = TestClient(create_api(a, "t"), base_url="http://127.0.0.1")
    return a, c, g, tmp_path


def test_connecting_youtube_and_syncing(yt):
    a, c, g, tmp = yt
    assert c.post("/api/connect/youtube/start", headers=H).status_code == 400            # no app yet
    assert c.post("/api/connect/youtube/app", json={"client_id": "x", "client_secret": "y"}, headers=H).status_code == 400
    r = c.post("/api/connect/youtube/app", json={"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-secret123"}, headers=H)
    assert r.json()["has_app"] and not r.json()["connected"]
    keys = tmp / "integrations" / "youtube.json"
    assert oct(keys.stat().st_mode)[-3:] == "600"
    url = c.post("/api/connect/youtube/start", headers=H).json()["url"]
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
    assert url.startswith("https://accounts.google.com/") and q["code_challenge_method"] == "S256" and q["access_type"] == "offline"
    assert q["redirect_uri"] == "http://127.0.0.1:8765/oauth/youtube" and "youtube.upload" in q["scope"]
    # A made-up state is refused; the real one connects.
    assert "not from this Convo" in c.get("/oauth/youtube?code=x&state=youtube.forged").text
    page = c.get(f"/oauth/youtube?code=good&state={q['state']}")
    assert "is connected" in page.text
    assert g.token_forms[-1]["code_verifier"] and g.token_forms[-1]["client_secret"] == "GOCSPX-secret123"
    assert "is connected" not in c.get(f"/oauth/youtube?code=good&state={q['state']}").text     # a state works once
    assert "at-1" in keys.read_text() and oct(keys.stat().st_mode)[-3:] == "600"
    # Sync: channel, videos, 28 days, traffic.
    out = c.post("/api/connect/youtube/sync", headers=H).json()
    assert out == {"channel": "Luke Horbach", "handle": "lukehorbach", "subscribers": 1234, "videos": 2, "days": 2}
    ov = c.get("/api/connect/youtube/overview", headers=H).json()
    assert ov["subscribers"] == 1234 and ov["views_total"] == 56789 and [v["title"] for v in ov["recent"]] == ["Derby day", "Exchange week"]
    assert ov["days"][0]["views"] == 120 and ov["traffic"]["sources"][0] == {"source": "YT_SEARCH", "views": 150}
    acct = a.store.one("SELECT * FROM accounts WHERE platform = 'youtube'")
    assert acct["handle"] == "lukehorbach" and acct["status"] == "connected"
    items = {i["id"]: i for i in c.get("/api/hub/integrations", headers=H).json()["items"]}
    assert items["youtube_api"]["status"] == "connected" and "Luke Horbach" in items["youtube_api"]["detail"]
    # Disconnect deletes the keys but keeps the app details.
    st = c.post("/api/connect/youtube/disconnect", headers=H).json()
    assert not st["connected"] and st["has_app"] and "at-1" not in keys.read_text()


def test_a_refused_sign_in_is_explained(yt):
    a, c, g, _ = yt
    c.post("/api/connect/youtube/app", json={"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-secret123"}, headers=H)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(c.post("/api/connect/youtube/start", headers=H).json()["url"]).query))
    assert "Bad code" in c.get(f"/oauth/youtube?code=bad&state={q['state']}").text
    assert "chose not to allow" in c.get("/oauth/youtube?error=access_denied").text


def test_uploading_a_video_as_private(yt):
    a, c, g, tmp = yt
    c.post("/api/connect/youtube/app", json={"client_id": "123-abc.apps.googleusercontent.com", "client_secret": "GOCSPX-secret123"}, headers=H)
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(c.post("/api/connect/youtube/start", headers=H).json()["url"]).query))
    c.get(f"/oauth/youtube?code=good&state={q['state']}")
    cid = c.post("/api/hub/content", json={"title": "Derby", "kind": "video", "platforms": ["youtube"],
                                           "versions": {"youtube": {"title": "Derby day", "description": "Sunday", "hashtags": ["football"]}}}, headers=H).json()["id"]
    assert c.post(f"/api/hub/content/{cid}/publish/youtube", headers=H).status_code == 400       # no video yet
    c.post(f"/api/hub/content/{cid}/upload?filename=clip.mp4", content=b"\x00" * 1000, headers=H)
    assert c.post(f"/api/hub/content/{cid}/publish/youtube", headers=H).status_code == 200
    import time
    for _ in range(50):
        s = c.get(f"/api/hub/content/{cid}/publish/youtube", headers=H).json()
        if s["done"]:
            break
        time.sleep(0.05)
    assert s["result"] == "newvid" and s["error"] is None and g.uploaded == b"\x00" * 1000
    assert g.meta["snippet"]["title"] == "Derby day" and g.meta["snippet"]["tags"] == ["football"]
    item = c.get(f"/api/hub/content/{cid}", headers=H).json()
    assert item["versions"]["youtube"]["uploaded_id"] == "newvid" and item["post_url"] == "https://youtu.be/newvid"
    assert item["status"] != "posted"                                                             # private: not public yet
