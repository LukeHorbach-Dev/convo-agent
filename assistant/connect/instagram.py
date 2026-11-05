"""Instagram, through the Instagram API with Instagram Login (needs a free Creator or Business account) and Luke's own
Meta developer app.

What it reads: the profile (followers, following, posts), the latest posts with likes, comments and, where Instagram
gives them, views, reach, saves and shares; and reach and new followers by day for the last 28 days.
What it writes: a Reel you press Publish on in the Content Studio (a public post, so Convo always asks first).
Photos need to be at a public web address for Instagram to fetch them, which a file on your Mac isn't, so for photos
Convo has the caption ready to paste instead.

It can't do what the official API doesn't allow: starting DMs (Hello Message keeps its careful browser method), or
the list of people you follow.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..store import Store, now_iso
from .base import ConnectError, Connection, Platform

INSTAGRAM = Platform(
    id="instagram", name="Instagram",
    auth_url="https://www.instagram.com/oauth/authorize", token_url="https://api.instagram.com/oauth/access_token",
    scopes=["instagram_business_basic", "instagram_business_content_publish", "instagram_business_manage_insights",
            "instagram_business_manage_comments"],
    redirect="relay", scope_sep=",", extra_auth={"enable_fb_login": "0", "force_authentication": "1"},
)
GRAPH = "https://graph.instagram.com"
V = "v23.0"


class InstagramConnection(Connection):
    refresh_before_s = 7 * 86400               # long-lived keys last 60 days; renew a week before

    def after_login(self, tokens: dict, d: dict) -> dict:
        """Swap the one-hour key for a 60-day one."""
        try:
            r = self.http.get(f"{GRAPH}/access_token", params={"grant_type": "ig_exchange_token", "client_secret": d["client_secret"],
                                                               "access_token": tokens["access_token"]})
            j = r.json()
        except Exception as e:
            raise ConnectError("Instagram didn't give Convo a lasting key. Press Connect again.") from e
        if r.status_code >= 400 or "access_token" not in j:
            raise ConnectError("Instagram didn't give Convo a lasting key. Press Connect again.")
        return {**tokens, "access_token": j["access_token"], "expires_at": time.time() + float(j.get("expires_in") or 5184000)}

    def request(self, method: str, url: str, **kw):
        """Instagram's Graph API also takes the key as access_token; send both, which it accepts."""
        if url.startswith(GRAPH):
            kw["params"] = {**(kw.get("params") or {}), "access_token": self.access_token()}
        return super().request(method, url, **kw)

    def refresh(self, t: dict, d: dict) -> dict:
        if t.get("expires_at", 0) < time.time():
            raise ConnectError("Instagram's key ran out. Press Connect again.")
        r = self.http.get(f"{GRAPH}/refresh_access_token", params={"grant_type": "ig_refresh_token", "access_token": t["access_token"]})
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code >= 400 or "access_token" not in j:
            return t                                    # keep using it; try again next time
        return {**t, "access_token": j["access_token"], "expires_at": time.time() + float(j.get("expires_in") or 5184000)}


def _ig_account(st: Store, handle: str, name: str | None) -> int:
    h = handle.lower()
    row = st.one("SELECT id FROM accounts WHERE platform = 'instagram' AND handle = ?", (h,))
    if row:
        st.execute("UPDATE accounts SET connection = CASE WHEN connection LIKE '%api%' THEN connection ELSE connection || ',api' END, "
                   "status = 'connected', display_name = COALESCE(display_name, ?), updated_at = ? WHERE id = ?", (name, now_iso(), row["id"]))
        return row["id"]
    return st.execute("INSERT INTO accounts(platform, handle, display_name, role, connection, status, status_note, created_at, updated_at) "
                      "VALUES ('instagram', ?, ?, 'other', 'api', 'connected', 'Instagram API', ?, ?)", (h, name, now_iso(), now_iso())).lastrowid


def sync(conn: Connection, st: Store) -> dict:
    from ..hub import record_snapshot
    me = conn.get(f"{GRAPH}/{V}/me", fields="user_id,username,name,account_type,followers_count,follows_count,media_count")
    if not me.get("username"):
        raise ConnectError("Instagram didn't say which account this is. Press Connect again.")
    aid = _ig_account(st, me["username"], me.get("name"))
    record_snapshot(st, aid, now_iso(), "instagram_api", me.get("followers_count"), me.get("follows_count"), me.get("media_count"))
    posts = 0
    media = conn.get(f"{GRAPH}/{V}/me/media", fields="id,caption,media_type,media_product_type,permalink,timestamp,thumbnail_url,"
                     "media_url,like_count,comments_count", limit=25).get("data", [])
    for m in media:
        ins = {}
        try:
            metrics = "views,reach,saved,shares,total_interactions"
            for x in conn.get(f"{GRAPH}/{V}/{m['id']}/insights", metric=metrics).get("data", []):
                ins[x["name"]] = (x.get("values") or [{}])[0].get("value", x.get("total_value", {}).get("value"))
        except ConnectError:
            pass                                          # older posts or some types don't have every number
        thumb = m.get("thumbnail_url") or (m.get("media_url") if m.get("media_type") != "VIDEO" else None)
        st.execute("""INSERT INTO platform_posts(platform, account_id, post_id, title, published_at, url, thumbnail, views, likes, comments, shares, extra, updated_at)
            VALUES ('instagram', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(platform, post_id) DO UPDATE SET title = excluded.title,
            views = excluded.views, likes = excluded.likes, comments = excluded.comments, shares = excluded.shares, thumbnail = excluded.thumbnail,
            extra = excluded.extra, updated_at = excluded.updated_at""",
                   (aid, m["id"], (m.get("caption") or "")[:200], m.get("timestamp"), m.get("permalink"), thumb, ins.get("views"),
                    m.get("like_count"), m.get("comments_count"), ins.get("shares"),
                    json.dumps({"type": m.get("media_product_type") or m.get("media_type"), "reach": ins.get("reach"), "saved": ins.get("saved")}),
                    now_iso()))
        posts += 1
    days = 0
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=28)
    for metric in ("reach", "follower_count"):
        try:
            data = conn.get(f"{GRAPH}/{V}/me/insights", metric=metric, period="day", since=int(since.timestamp()), until=int(until.timestamp())).get("data", [])
        except ConnectError:
            continue                                      # follower_count needs 100+ followers
        for series in data:
            for v in series.get("values", []):
                day = (v.get("end_time") or "")[:10]
                if not day:
                    continue
                old = st.one("SELECT metrics FROM platform_daily WHERE platform = 'instagram' AND account_id = ? AND date = ?", (aid, day))
                m = {**(json.loads(old["metrics"]) if old else {}), metric: v.get("value")}
                st.execute("INSERT OR REPLACE INTO platform_daily(platform, account_id, date, metrics) VALUES ('instagram', ?, ?, ?)",
                           (aid, day, json.dumps(m)))
                days += 1
    conn.vault.update(account={"id": me.get("user_id") or me.get("id"), "handle": me["username"], "name": me.get("name") or me["username"],
                               "account_id": aid, "type": me.get("account_type")}, last_sync=now_iso(), error=None)
    return {"handle": me["username"], "followers": me.get("followers_count"), "posts": posts, "days": days}


def publish_reel(conn: Connection, st: Store, item: dict, file: Path, progress=lambda pct: None, wait_s: float = 5.0) -> str:
    """Publish a post's video as a Reel, with its Instagram caption. Public, so only after Luke presses Publish."""
    v = (json.loads(item.get("versions") or "{}") or {}).get("instagram") or {}
    caption = "\n\n".join(x for x in [v.get("caption") or "", " ".join(f"#{t}" for t in v.get("hashtags") or [])] if x)[:2200]
    c = conn.request("POST", f"{GRAPH}/{V}/me/media", data={"media_type": "REELS", "upload_type": "resumable", "caption": caption}).json()
    cid, uri = c.get("id"), c.get("uri")
    if not cid or not uri:
        raise ConnectError("Instagram didn't start the upload. Try again.")
    size = os.path.getsize(file)
    with open(file, "rb") as f:
        r = conn.http.post(uri, content=f.read(), headers={"Authorization": f"OAuth {conn.access_token()}", "offset": "0",
                                                           "file_size": str(size)}, timeout=900)
    if r.status_code >= 400:
        raise ConnectError(f"Instagram didn't take the video ({r.status_code}).")
    progress(60)
    for _ in range(120):                                  # Instagram processes the video before it can be published
        s = conn.get(f"{GRAPH}/{V}/{cid}", fields="status_code,status")
        code = s.get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            raise ConnectError(f"Instagram couldn't process the video ({s.get('status') or code}).")
        time.sleep(wait_s)
    else:
        raise ConnectError("Instagram is still processing the video. Try Publish again in a few minutes.")
    progress(90)
    pub = conn.request("POST", f"{GRAPH}/{V}/me/media_publish", data={"creation_id": cid}).json()
    if not pub.get("id"):
        raise ConnectError("Instagram didn't publish it. Try again.")
    progress(100)
    try:
        return conn.get(f"{GRAPH}/{V}/{pub['id']}", fields="permalink").get("permalink") or pub["id"]
    except ConnectError:
        return pub["id"]
