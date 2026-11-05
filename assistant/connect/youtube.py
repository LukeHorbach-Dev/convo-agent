"""YouTube, through the YouTube Data API and the YouTube Analytics API, with Luke's own Google Cloud project.

What it reads: the channel (subscribers, views, number of videos), the latest videos with their views, likes and
comments, and the last 28 days by day (views, watch time, average view length, subscribers gained and lost) plus
where views came from (search, suggested, external and so on).
What it writes: a video you choose to upload from the Content Studio, uploaded as **private** (Google keeps uploads
from apps it hasn't audited private anyway). You make it public in YouTube Studio when you're happy.

Google's daily allowance is 10,000 units: a sync uses about 5, an upload about 1,600 (so about 6 uploads a day).
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path

from ..store import Store, now_iso
from .base import ConnectError, Connection, Platform

YOUTUBE = Platform(
    id="youtube", name="YouTube",
    auth_url="https://accounts.google.com/o/oauth2/v2/auth", token_url="https://oauth2.googleapis.com/token",
    scopes=["https://www.googleapis.com/auth/youtube.readonly", "https://www.googleapis.com/auth/yt-analytics.readonly",
            "https://www.googleapis.com/auth/youtube.upload"],
    extra_auth={"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"},
)
DATA = "https://www.googleapis.com/youtube/v3"
ANALYTICS = "https://youtubeanalytics.googleapis.com/v2/reports"
UPLOAD = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK = 8 * 1024 * 1024


def _int(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def account_for(st: Store, handle: str, name: str | None) -> int:
    """The Convo account row for this channel: an existing YouTube account with that handle, or a new one."""
    h = handle.lstrip("@").lower()
    row = st.one("SELECT id FROM accounts WHERE platform = 'youtube' AND handle = ?", (h,))
    if row:
        st.execute("UPDATE accounts SET connection = 'api', status = 'connected', status_note = 'YouTube API', "
                   "display_name = COALESCE(display_name, ?), updated_at = ? WHERE id = ?", (name, now_iso(), row["id"]))
        return row["id"]
    return st.execute("INSERT INTO accounts(platform, handle, display_name, role, purpose, connection, status, status_note, created_at, updated_at) "
                      "VALUES ('youtube', ?, ?, 'other', 'My YouTube channel', 'api', 'connected', 'YouTube API', ?, ?)",
                      (h, name, now_iso(), now_iso())).lastrowid


def sync(conn: Connection, st: Store) -> dict:
    """Channel, latest videos and the last 28 days. Returns a short summary."""
    from ..hub import record_snapshot
    ch = conn.get(f"{DATA}/channels", part="snippet,statistics,contentDetails", mine="true").get("items") or []
    if not ch:
        raise ConnectError("This Google account has no YouTube channel yet. Make one on YouTube, then Sync again.")
    c = ch[0]
    stats, snip = c.get("statistics", {}), c.get("snippet", {})
    handle = (snip.get("customUrl") or c["id"]).lstrip("@")
    aid = account_for(st, handle, snip.get("title"))
    subs = None if stats.get("hiddenSubscriberCount") else _int(stats.get("subscriberCount"))
    record_snapshot(st, aid, now_iso(), "youtube_api", subs, None, _int(stats.get("videoCount")), {"views": _int(stats.get("viewCount"))})

    uploads = c.get("contentDetails", {}).get("relatedPlaylists", {}).get("uploads")
    videos = 0
    if uploads:
        ids = [i["contentDetails"]["videoId"] for i in conn.get(f"{DATA}/playlistItems", part="contentDetails", playlistId=uploads,
                                                                 maxResults=25).get("items", [])]
        if ids:
            for v in conn.get(f"{DATA}/videos", part="snippet,statistics,contentDetails,status", id=",".join(ids)).get("items", []):
                s, vs = v.get("snippet", {}), v.get("statistics", {})
                thumb = (s.get("thumbnails", {}).get("medium") or s.get("thumbnails", {}).get("default") or {}).get("url")
                st.execute("""INSERT INTO platform_posts(platform, account_id, post_id, title, published_at, url, thumbnail, views, likes, comments, extra, updated_at)
                    VALUES ('youtube', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(platform, post_id) DO UPDATE SET
                    title = excluded.title, views = excluded.views, likes = excluded.likes, comments = excluded.comments,
                    thumbnail = excluded.thumbnail, extra = excluded.extra, updated_at = excluded.updated_at""",
                           (aid, v["id"], s.get("title"), s.get("publishedAt"), f"https://youtu.be/{v['id']}", thumb,
                            _int(vs.get("viewCount")), _int(vs.get("likeCount")), _int(vs.get("commentCount")),
                            json.dumps({"duration": v.get("contentDetails", {}).get("duration"), "privacy": v.get("status", {}).get("privacyStatus")}),
                            now_iso()))
                videos += 1

    end = date.today()
    start = end - timedelta(days=28)
    days = 0
    try:
        rep = conn.get(ANALYTICS, ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(), dimensions="day",
                       metrics="views,estimatedMinutesWatched,averageViewDuration,subscribersGained,subscribersLost", sort="day")
        cols = [h["name"] for h in rep.get("columnHeaders", [])]
        for row in rep.get("rows", []) or []:
            m = dict(zip(cols, row))
            st.execute("INSERT OR REPLACE INTO platform_daily(platform, account_id, date, metrics) VALUES ('youtube', ?, ?, ?)",
                       (aid, m.pop("day"), json.dumps(m)))
            days += 1
        src = conn.get(ANALYTICS, ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
                       dimensions="insightTrafficSourceType", metrics="views", sort="-views")
        st.set("youtube_traffic", {"from": start.isoformat(), "to": end.isoformat(),
                                   "sources": [{"source": r[0], "views": r[1]} for r in src.get("rows", []) or []]})
    except ConnectError as e:
        st.log("warn", f"YouTube Analytics didn't answer ({e}); the channel and videos were still updated.")
    summary = {"channel": snip.get("title"), "handle": handle, "subscribers": subs, "videos": videos, "days": days}
    conn.vault.update(account={"id": c["id"], "handle": handle, "name": snip.get("title"), "account_id": aid},
                      last_sync=now_iso(), error=None)
    return summary


def upload(conn: Connection, st: Store, item: dict, file: Path, progress=lambda pct: None) -> str:
    """Upload a post's video as private, with its YouTube title, description and tags. Returns the video id."""
    v = (json.loads(item.get("versions") or "{}") or {}).get("youtube") or {}
    title = (v.get("title") or item.get("title") or "Untitled").strip()[:100]
    body = {"snippet": {"title": title, "description": (v.get("description") or "")[:5000], "tags": (v.get("hashtags") or [])[:15],
                        "categoryId": "22"},
            "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False}}
    size = os.path.getsize(file)
    r = conn.request("POST", UPLOAD, params={"uploadType": "resumable", "part": "snippet,status"}, json=body,
                     headers={"X-Upload-Content-Length": str(size), "X-Upload-Content-Type": item.get("media_type") or "video/mp4"})
    where = r.headers.get("location")
    if not where:
        raise ConnectError("YouTube didn't start the upload. Try again.")
    sent = 0
    with open(file, "rb") as f:
        while sent < size:
            chunk = f.read(CHUNK)
            end = sent + len(chunk) - 1
            r = conn.http.put(where, content=chunk, headers={"Authorization": f"Bearer {conn.access_token()}",
                                                             "Content-Range": f"bytes {sent}-{end}/{size}"}, timeout=600)
            if r.status_code in (200, 201):
                vid = r.json()["id"]
                progress(100)
                return vid
            if r.status_code != 308:
                raise ConnectError(f"The upload stopped (YouTube said {r.status_code}). Try again.")
            sent = end + 1
            progress(int(sent * 100 / size))
    raise ConnectError("The upload didn't finish. Try again.")
