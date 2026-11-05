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
from . import youtube
from .base import ConnectError, Connection

SYNC_EVERY = timedelta(hours=6)


class Connections:
    def __init__(self, folder: Path, port: int, log, http=None):
        self.all: dict[str, Connection] = {"youtube": Connection(youtube.YOUTUBE, folder, port, http=http, log=log)}
        self.uploads: dict[int, dict] = {}                     # content id -> {"pct", "error", "done", "video_id"}

    def __getitem__(self, pid: str) -> Connection:
        if pid not in self.all:
            raise HTTPException(404, "Convo doesn't connect to that yet.")
        return self.all[pid]

    def sync(self, pid: str, st: Store) -> dict:
        c = self[pid]
        with c.busy:
            try:
                out = {"youtube": youtube.sync}[pid](c, st)
            except ConnectError as e:
                c.vault.update(error=str(e))
                raise
        st.log("good", f"{c.p.name} updated" + (f": {out.get('subscribers'):,} subscribers." if out.get("subscribers") is not None else "."))
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

    @api.get("/api/connect", dependencies=dep)
    def statuses():
        return {"items": [c.status() for c in cx.all.values()]}

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

    @api.post("/api/hub/content/{cid}/publish/youtube", dependencies=dep)
    def publish_youtube(cid: int):
        item = st.one("SELECT * FROM content WHERE id = ?", (cid,))
        if item is None:
            raise HTTPException(404, "That post isn't there any more.")
        if not (item["media_type"] or "").startswith("video/"):
            raise HTTPException(400, "YouTube needs a video file. Add one in the Studio first.")
        if not cx["youtube"].status()["connected"]:
            raise HTTPException(400, "Connect YouTube first (Integrations).")
        if cx.uploads.get(cid, {}).get("pct") is not None and not cx.uploads[cid].get("done"):
            raise HTTPException(409, "It's already uploading.")
        path = Path(a.s.data_dir) / "library" / str(cid) / item["media"]
        state = cx.uploads[cid] = {"pct": 0, "error": None, "done": False, "video_id": None}

        def run():
            try:
                vid = youtube.upload(cx["youtube"], st, item, path, progress=lambda p: state.update(pct=p))
                versions = json.loads(item["versions"] or "{}")
                versions.setdefault("youtube", {}).update(uploaded_id=vid, privacy="private", uploaded_at=now_iso())
                st.execute("UPDATE content SET versions = ?, post_url = COALESCE(post_url, ?), updated_at = ? WHERE id = ?",
                           (json.dumps(versions), f"https://youtu.be/{vid}", now_iso(), cid))
                st.log("good", f"Uploaded {item['title'] or 'your video'} to YouTube as private. Make it public in YouTube Studio when you're ready.")
                state.update(done=True, video_id=vid, pct=100)
            except (ConnectError, OSError) as e:
                state.update(error=str(e), done=True)
                st.log("warn", f"The YouTube upload of {item['title'] or 'your video'} stopped: {e}")
        threading.Thread(target=run, name="youtube-upload", daemon=True).start()
        return state

    @api.get("/api/hub/content/{cid}/publish/youtube", dependencies=dep)
    def publish_youtube_state(cid: int):
        return cx.uploads.get(cid) or {"pct": None, "error": None, "done": False, "video_id": None}


def _quiet_sync(cx: Connections, pid: str, st: Store) -> None:
    try:
        cx.sync(pid, st)
    except ConnectError as e:
        st.log("warn", f"Connected, but couldn't fetch the numbers yet ({e}).")
