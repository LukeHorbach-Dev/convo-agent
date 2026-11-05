"""TikTok, through Login Kit, the Display API and the Content Posting API, with Luke's own TikTok developer app.

What it reads: the profile (followers, following, likes, number of videos) and the latest videos with views, likes,
comments and shares. TikTok's API doesn't give watch time, retention or where viewers came from.
What it writes: a video you choose from the Content Studio is sent to your TikTok inbox as a draft ("Upload"). You
finish it in the TikTok app (sound, cover, who can see it) and post it yourself, so nothing goes public by itself.
Until TikTok reviews your app, it works with your own account only, which is all Convo needs.
"""
from __future__ import annotations

import math
import os
from pathlib import Path

from ..store import Store, now_iso
from .base import ConnectError, Connection, Platform

TIKTOK = Platform(
    id="tiktok", name="TikTok",
    auth_url="https://www.tiktok.com/v2/auth/authorize/", token_url="https://open.tiktokapis.com/v2/oauth/token/",
    scopes=["user.info.basic", "user.info.profile", "user.info.stats", "video.list", "video.upload"],
    redirect="relay", scope_sep=",", client_id_param="client_key",
)
API = "https://open.tiktokapis.com/v2"
CHUNK = 10 * 1024 * 1024


def _check(j: dict) -> dict:
    err = j.get("error") or {}
    if err.get("code") not in (None, "ok"):
        raise ConnectError(f"TikTok said no ({err.get('message') or err.get('code')}).")
    return j.get("data") or {}


def sync(conn: Connection, st: Store) -> dict:
    from ..hub import record_snapshot
    u = _check(conn.get(f"{API}/user/info/", fields="open_id,display_name,username,follower_count,following_count,likes_count,video_count")).get("user") or {}
    handle = (u.get("username") or u.get("display_name") or "tiktok").lower()
    row = st.one("SELECT id FROM accounts WHERE platform = 'tiktok' AND handle = ?", (handle,))
    if row:
        aid = row["id"]
        st.execute("UPDATE accounts SET connection = 'api', status = 'connected', status_note = 'TikTok API', updated_at = ? WHERE id = ?", (now_iso(), aid))
    else:
        aid = st.execute("INSERT INTO accounts(platform, handle, display_name, role, connection, status, status_note, created_at, updated_at) "
                         "VALUES ('tiktok', ?, ?, 'other', 'api', 'connected', 'TikTok API', ?, ?)", (handle, u.get("display_name"), now_iso(), now_iso())).lastrowid
    record_snapshot(st, aid, now_iso(), "tiktok_api", u.get("follower_count"), u.get("following_count"), u.get("video_count"),
                    {"likes": u.get("likes_count")})
    vids = _check(conn.request("POST", f"{API}/video/list/", params={"fields": "id,title,video_description,create_time,cover_image_url,share_url,"
                                                                              "view_count,like_count,comment_count,share_count,duration"},
                               json={"max_count": 20}).json()).get("videos") or []
    from datetime import datetime, timezone
    for v in vids:
        when = datetime.fromtimestamp(v["create_time"], timezone.utc).isoformat(timespec="seconds") if v.get("create_time") else None
        st.execute("""INSERT INTO platform_posts(platform, account_id, post_id, title, published_at, url, thumbnail, views, likes, comments, shares, extra, updated_at)
            VALUES ('tiktok', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', ?) ON CONFLICT(platform, post_id) DO UPDATE SET title = excluded.title,
            views = excluded.views, likes = excluded.likes, comments = excluded.comments, shares = excluded.shares,
            thumbnail = excluded.thumbnail, updated_at = excluded.updated_at""",
                   (aid, v["id"], (v.get("title") or v.get("video_description") or "")[:200], when, v.get("share_url"), v.get("cover_image_url"),
                    v.get("view_count"), v.get("like_count"), v.get("comment_count"), v.get("share_count"), now_iso()))
    conn.vault.update(account={"id": u.get("open_id"), "handle": handle, "name": u.get("display_name") or handle, "account_id": aid},
                      last_sync=now_iso(), error=None)
    return {"handle": handle, "followers": u.get("follower_count"), "videos": len(vids)}


def upload_draft(conn: Connection, st: Store, item: dict, file: Path, progress=lambda pct: None) -> str:
    """Send the video to Luke's TikTok inbox as a draft. Returns TikTok's publish id."""
    size = os.path.getsize(file)
    chunk = size if size <= CHUNK else CHUNK
    count = max(1, math.floor(size / chunk)) if size > CHUNK else 1
    init = _check(conn.request("POST", f"{API}/post/publish/inbox/video/init/", json={"source_info": {
        "source": "FILE_UPLOAD", "video_size": size, "chunk_size": chunk, "total_chunk_count": count}}).json())
    url, pid = init.get("upload_url"), init.get("publish_id")
    if not url or not pid:
        raise ConnectError("TikTok didn't start the upload. Try again.")
    with open(file, "rb") as f:
        for i in range(count):
            start = i * chunk
            data = f.read(chunk if i < count - 1 else size - start)        # the last chunk takes the rest
            end = start + len(data) - 1
            r = conn.http.put(url, content=data, headers={"Content-Type": item.get("media_type") or "video/mp4",
                                                          "Content-Range": f"bytes {start}-{end}/{size}"}, timeout=900)
            if r.status_code not in (200, 201, 206):
                raise ConnectError(f"The upload stopped (TikTok said {r.status_code}). Try again.")
            progress(int((end + 1) * 100 / size))
    return pid
