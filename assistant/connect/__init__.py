"""The platform connections and their web API. See base.py for how signing in works."""
from __future__ import annotations

import html
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from ..store import Store, now_iso
from . import instagram, tiktok, youtube
from .base import ConnectError, Connection

SYNCS = {"youtube": youtube.sync, "instagram": instagram.sync, "tiktok": tiktok.sync}
# What Publish does on each platform, and how Convo describes it before asking.
PUBLISH = {
    "youtube": (lambda c, st, item, path, prog: youtube.upload(c, st, item, path, prog), "private",
                "It goes up as private, with this post's YouTube title, description and tags. Only you can see it until you make it public in YouTube Studio."),
    "instagram": (lambda c, st, item, path, prog: instagram.publish_reel(c, st, item, path, prog), "public",
                  "It's posted as a Reel on your Instagram straight away, with this post's Instagram caption. Everyone can see it."),
    "tiktok": (lambda c, st, item, path, prog: tiktok.upload_draft(c, st, item, path, prog), "draft",
               "It goes to your TikTok inbox as a draft. You finish it in the TikTok app (sound, cover, who sees it) and post it yourself."),
}

SYNC_EVERY = timedelta(hours=6)


class Connections:
    def __init__(self, folder: Path, port: int, log, http=None):
        self.all: dict[str, Connection] = {
            "youtube": Connection(youtube.YOUTUBE, folder, port, http=http, log=log),
            "instagram": instagram.InstagramConnection(instagram.INSTAGRAM, folder, port, http=http, log=log),
            "tiktok": Connection(tiktok.TIKTOK, folder, port, http=http, log=log),
        }
        self.uploads: dict[tuple[int, str], dict] = {}         # (content id, platform) -> {"pct", "error", "done", "result"}

    def __getitem__(self, pid: str) -> Connection:
        if pid not in self.all:
            raise HTTPException(404, "Convo doesn't connect to that yet.")
        return self.all[pid]

    def sync(self, pid: str, st: Store) -> dict:
        c = self[pid]
        with c.busy:
            try:
                out = SYNCS[pid](c, st)
            except ConnectError as e:
                c.vault.update(error=str(e))
                raise
            if pid in ("instagram", "youtube"):
                from ..engagement import fetch_instagram, fetch_youtube
                try:
                    out["new_comments"] = (fetch_instagram if pid == "instagram" else fetch_youtube)(c, st)
                except ConnectError:
                    pass
        n = out.get("subscribers") if out.get("subscribers") is not None else out.get("followers")
        st.log("good", f"{c.p.name} updated" + (f": {n:,} {'subscribers' if pid == 'youtube' else 'followers'}." if n is not None else "."))
        return out

    def sync_due(self, st: Store) -> None:
        for pid, c in self.all.items():
            s = c.status()
            if not s["connected"] or s["error"]:
                continue
            last = s["last_sync"]
            if last and datetime.now(timezone.utc) - datetime.fromisoformat(last) < SYNC_EVERY:
                continue
            try:
                self.sync(pid, st)
            except ConnectError as e:
                st.log("warn", f"Couldn't update {c.p.name} ({e}).")


class AppBody(BaseModel):
    client_id: str
    client_secret: str


def _page(title: str, text: str, good: bool) -> HTMLResponse:
    color = "#34d399" if good else "#f87171"
    return HTMLResponse(f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Convo</title><body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#0a0b0f;color:#e7e9ee;font:16px/1.5 -apple-system,system-ui,sans-serif">
<div style="max-width:420px;padding:32px;text-align:center"><div style="font-size:40px;color:{color}">{'✓' if good else '!'}</div>
<h1 style="font-size:22px;margin:8px 0">{html.escape(title)}</h1><p style="color:#9aa0ad">{html.escape(text)}</p></div></body>""",
                        status_code=200 if good else 400)


def register(api: FastAPI, a, auth) -> None:
    st: Store = a.store
    dep = [Depends(auth)]
    cx: Connections = a.connect

    def ok(fn):
        try:
            return fn()
        except ConnectError as e:
            raise HTTPException(400, str(e))

    relay = {"ok": None, "at": 0.0}

    def relay_ok() -> bool | None:
        """Is the https return page (GitHub Pages) up? Checked at most every 10 minutes."""
        import time as _t
        from .base import RELAY
        if _t.time() - relay["at"] > 600 or (relay["ok"] is False and _t.time() - relay["at"] > 30):
            try:
                r = cx.all["tiktok"].http.get(RELAY, timeout=6)
                relay["ok"] = r.status_code == 200 and "Going back to Convo" in r.text
            except Exception:
                relay["ok"] = False
            relay["at"] = _t.time()
        return relay["ok"]

    @api.get("/api/connect", dependencies=dep)
    def statuses():
        items = [c.status() for c in cx.all.values()]
        if any(c.p.redirect == "relay" and not c.status()["connected"] for c in cx.all.values()):
            for i in items:
                if cx.all[i["id"]].p.redirect == "relay":
                    i["relay_ok"] = relay_ok()
        return {"items": items}

    @api.post("/api/connect/{pid}/app", dependencies=dep)
    def save_app(pid: str, body: AppBody):
        ok(lambda: cx[pid].save_app(body.client_id, body.client_secret))
        st.log("info", f"Saved your {cx[pid].p.name} app details.")
        return cx[pid].status()

    @api.post("/api/connect/{pid}/start", dependencies=dep)
    def start(pid: str):
        return {"url": ok(lambda: cx[pid].start())}

    @api.get("/oauth/{pid}")
    def oauth_return(pid: str, code: str = "", state: str = "", error: str = "", error_description: str = ""):
        """Where the platform sends you back after signing in. Checked against the sign-in Convo started."""
        if pid not in cx.all:
            return _page("Not something Convo connects to", "Go back to Convo and try again.", False)
        c = cx.all[pid]
        if error:
            return _page(f"{c.p.name} wasn't connected", error_description or "You chose not to allow it, or it was cancelled. "
                         "Go back to Convo and press Connect to try again.", False)
        try:
            c.finish(code, state)
        except ConnectError as e:
            return _page(f"{c.p.name} wasn't connected", str(e), False)
        threading.Thread(target=lambda: _quiet_sync(cx, pid, st), daemon=True).start()
        return _page(f"{c.p.name} is connected", "You can close this tab and go back to Convo. It's fetching your numbers now.", True)

    @api.post("/api/connect/{pid}/sync", dependencies=dep)
    def sync_now(pid: str):
        return ok(lambda: cx.sync(pid, st))

    @api.post("/api/connect/{pid}/disconnect", dependencies=dep)
    def disconnect(pid: str):
        cx[pid].disconnect()
        return cx[pid].status()

    @api.get("/api/connect/youtube/overview", dependencies=dep)
    def youtube_overview():
        s = cx["youtube"].status()
        acct = (s.get("account") or {}).get("account_id")
        if not acct:
            return {"connected": s["connected"], "channel": None}
        snaps = st.all("SELECT at, followers, posts, extra FROM snapshots WHERE account_id = ? ORDER BY at", (acct,))
        last = snaps[-1] if snaps else None
        days = [{"date": r["date"], **json.loads(r["metrics"])} for r in
                st.all("SELECT date, metrics FROM platform_daily WHERE platform = 'youtube' AND account_id = ? ORDER BY date DESC LIMIT 28", (acct,))][::-1]
        return {"connected": s["connected"], "channel": s["account"], "last_sync": s["last_sync"], "error": s["error"],
                "subscribers": last and last["followers"], "videos": last and last["posts"],
                "views_total": last and json.loads(last["extra"] or "{}").get("views"),
                "history": [{"at": r["at"], "subscribers": r["followers"]} for r in snaps],
                "days": days, "traffic": st.get("youtube_traffic"),
                "recent": st.all("SELECT post_id, title, published_at, url, thumbnail, views, likes, comments, extra FROM platform_posts "
                                 "WHERE platform = 'youtube' ORDER BY published_at DESC LIMIT 12")}

    @api.get("/api/connect/{pid}/overview", dependencies=dep)
    def overview(pid: str):
        if pid == "youtube":
            return youtube_overview()
        s = cx[pid].status()
        acct = (s.get("account") or {}).get("account_id")
        if not acct:
            return {"connected": s["connected"], "account": None}
        snaps = st.all("SELECT at, followers, following, posts, extra FROM snapshots WHERE account_id = ? ORDER BY at", (acct,))
        last = snaps[-1] if snaps else None
        days = [{"date": r["date"], **json.loads(r["metrics"])} for r in
                st.all("SELECT date, metrics FROM platform_daily WHERE platform = ? AND account_id = ? ORDER BY date DESC LIMIT 28", (pid, acct))][::-1]
        return {"connected": s["connected"], "account": s["account"], "last_sync": s["last_sync"], "error": s["error"],
                "followers": last and last["followers"], "following": last and last["following"], "posts": last and last["posts"],
                "likes_total": last and json.loads(last["extra"] or "{}").get("likes"),
                "history": [{"at": r["at"], "followers": r["followers"]} for r in snaps], "days": days,
                "recent": st.all("SELECT post_id, title, published_at, url, thumbnail, views, likes, comments, shares, extra FROM platform_posts "
                                 "WHERE platform = ? ORDER BY published_at DESC LIMIT 12", (pid,))}

    @api.post("/api/hub/content/{cid}/publish/{pid}", dependencies=dep)
    def publish(cid: int, pid: str):
        if pid not in PUBLISH:
            raise HTTPException(404, "Convo can't publish there.")
        item = st.one("SELECT * FROM content WHERE id = ?", (cid,))
        if item is None:
            raise HTTPException(404, "That post isn't there any more.")
        name = cx[pid].p.name
        if not (item["media_type"] or "").startswith("video/"):
            raise HTTPException(400, f"{name} needs a video file here. Add one in the Studio first." if pid != "instagram" else
                                "Convo can publish Reels (videos) to Instagram. For photos, copy the caption and post from your phone.")
        if not cx[pid].status()["connected"]:
            raise HTTPException(400, f"Connect {name} first (Integrations).")
        key = (cid, pid)
        if cx.uploads.get(key, {}).get("pct") is not None and not cx.uploads[key].get("done"):
            raise HTTPException(409, "It's already uploading.")
        path = Path(a.s.data_dir) / "library" / str(cid) / item["media"]
        fn, kind, _ = PUBLISH[pid]
        state = cx.uploads[key] = {"pct": 0, "error": None, "done": False, "result": None}

        def run():
            try:
                result = fn(cx[pid], st, item, path, lambda p: state.update(pct=p))
                row = st.one("SELECT versions, status FROM content WHERE id = ?", (cid,))
                versions = json.loads(row["versions"] or "{}")
                versions.setdefault(pid, {}).update(published=result, published_as=kind, published_at=now_iso())
                if pid == "youtube":
                    versions["youtube"]["uploaded_id"] = result
                fields = {"versions": json.dumps(versions), "updated_at": now_iso()}
                url = (f"https://youtu.be/{result}" if pid == "youtube" else result if str(result).startswith("https://") else None)
                st.execute("UPDATE content SET versions = ?, updated_at = ?, post_url = COALESCE(post_url, ?) WHERE id = ?",
                           (fields["versions"], fields["updated_at"], url, cid))
                if kind == "public":
                    st.execute("UPDATE content SET status = 'posted', posted_at = ? WHERE id = ?", (now_iso(), cid))
                st.log("good", {"private": f"Uploaded {item['title'] or 'your video'} to YouTube as private. Make it public in YouTube Studio when you're ready.",
                                "public": f"Posted {item['title'] or 'your Reel'} on Instagram.",
                                "draft": f"Sent {item['title'] or 'your video'} to your TikTok inbox. Finish it in the TikTok app."}[kind])
                state.update(done=True, result=result, pct=100)
            except (ConnectError, OSError) as e:
                state.update(error=str(e), done=True)
                st.log("warn", f"Publishing {item['title'] or 'your post'} to {name} stopped: {e}")
        threading.Thread(target=run, name=f"publish-{pid}", daemon=True).start()
        return state

    @api.get("/api/hub/content/{cid}/publish/{pid}", dependencies=dep)
    def publish_state(cid: int, pid: str):
        return cx.uploads.get((cid, pid)) or {"pct": None, "error": None, "done": False, "result": None}

    @api.get("/api/connect/publish-info", dependencies=dep)
    def publish_info():
        return {pid: {"kind": kind, "explain": explain, "connected": cx[pid].status()["connected"]} for pid, (_, kind, explain) in PUBLISH.items()}


def _quiet_sync(cx: Connections, pid: str, st: Store) -> None:
    try:
        cx.sync(pid, st)
    except ConnectError as e:
        st.log("warn", f"Connected, but couldn't fetch the numbers yet ({e}).")
