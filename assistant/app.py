"""Ties the pieces together: the store, the AI, the sender, and the jobs the dashboard starts (load a download,
write replies). Also the dashboard's web API."""
from __future__ import annotations

import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, SecretStr

from agent.intel.llm import build_provider
from agent.intel.profile import Fact, Profile
from agent.intel.reasoning import POLICY_VIOLATIONS
from agent.notify import mac_notify

from . import __version__
from .brain import never_list, prepare_hellos, think
from .config import AssistantSettings
from .export import ExportError, apply_export, find_downloads, read_export
from .sender import Sender
from .store import Store, now_iso

WEB = Path(__file__).parent / "web"


def save_env(values: dict[str, str], path: Path = Path(".env")) -> None:
    """Set keys in the local .env, kept owner-only because it can hold the API key."""
    lines = [l for l in (path.read_text().splitlines() if path.exists() else []) if l.split("=", 1)[0].strip() not in values]
    path.write_text("\n".join(lines + [f"{k}={v}" for k, v in values.items()]) + "\n")
    path.chmod(0o600)


def load_or_create_token(data_dir: Path) -> str:
    path = Path(data_dir) / "dashboard-token"
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token)
    path.chmod(0o600)
    return token


class Assistant:
    def __init__(self, settings: AssistantSettings, store: Store | None = None, make_web=None, llm=None, notify=mac_notify):
        self.s = settings
        self.store = store or Store(settings.db_path)
        self.notify = notify
        self.llm = llm or build_provider(settings.llm)
        self.make_web = make_web or self._instagram
        self.sender = Sender(self.store, settings, self.make_web, notify=notify)
        self.job = {"name": None, "done": 0, "total": 0, "error": None, "started": None}
        self._job_lock = threading.Lock()
        self._stop_job = threading.Event()

    def _instagram(self):
        from agent.platform.browser import BrowserSupervisor
        from agent.platform.sites import INSTAGRAM
        from agent.platform.web import WebPlatform
        sup = BrowserSupervisor(self.s.browser, INSTAGRAM)
        return WebPlatform(sup, INSTAGRAM, self.s.account_username)

    @property
    def manual(self) -> bool:
        return getattr(self.llm, "name", "") == "fake"

    def profile(self) -> Profile:
        return Profile.load(self.s.profile_path)

    # ------------------------------------------------------------------ jobs
    def _run_job(self, name: str, fn) -> bool:
        if not self._job_lock.acquire(blocking=False):
            return False
        self.job = {"name": name, "done": 0, "total": 0, "error": None, "started": now_iso()}
        self._stop_job.clear()

        def run():
            try:
                fn()
            except Exception as e:
                self.job["error"] = str(e)
                self.store.log("problem", f"{name.capitalize()} stopped: {e}")
            finally:
                self.job["name"] = None
                self._job_lock.release()
        threading.Thread(target=run, name=name, daemon=True).start()
        return True

    def load_download(self, paths: list[Path] | None = None, then_think: bool = True) -> bool:
        def work():
            files = paths or find_downloads(self.s.downloads_dir)
            if not files:
                raise ExportError("no Instagram download found in Downloads (a file starting with instagram-)")
            self.store.log("info", "Loading " + ", ".join(Path(f).name for f in files) + " ...")
            data = read_export([Path(f) for f in files])
            apply_export(self.store, data, self.s.account_username)
            prepare_hellos(self.store, self.profile(), never_list(self.s.never_list_path))
            if then_think:
                self.job["name"] = "writing replies"
                self._think_now()
        return self._run_job("loading your download", work)

    def write_replies(self) -> bool:
        return self._run_job("writing replies", self._think_now)

    def _think_now(self) -> None:
        def progress(done, total):
            self.job["done"], self.job["total"] = done, total
        out = think(self.store, self.profile(), self.llm, never_list(self.s.never_list_path),
                    window_days=self.s.reply_window_days, manual=self.manual, min_age=self.s.min_age,
                    progress=progress, stop=self._stop_job.is_set)
        ready = self.store.scalar("SELECT COUNT(*) FROM drafts WHERE status = 'draft'")
        if ready:
            self.notify("Instagram assistant", f"{ready} messages ready for you to check")

    def stop_job(self) -> None:
        self._stop_job.set()


# ---------------------------------------------------------------------- web API
class TextBody(BaseModel):
    text: str | None = None
    action: str                      # approve | unapprove | skip | restore | save


class BulkBody(BaseModel):
    ids: list[int]
    action: str


class UnfollowBody(BaseModel):
    usernames: list[str] = []
    everyone: bool = False
    confirm: str = ""


class NoteBody(BaseModel):
    note: str


class ImportBody(BaseModel):
    path: str | None = None


class AboutBody(BaseModel):
    facts: list[dict]                # {"id"?, "text", "private"}
    hellos: list[str]
    voice: str
    never: str


class SettingsBody(BaseModel):
    daily_messages: int | None = None
    daily_unfollows: int | None = None
    message_gap_s: float | None = None
    unfollow_gap_s: float | None = None
    reply_window_days: int | None = None
    ai_key: str | None = None
    tuck_window: bool | None = None


def policy_problems(text: str) -> list[str]:
    return [name for rx, name in POLICY_VIOLATIONS if rx.search(text)]


def create_api(a: Assistant, token: str) -> FastAPI:
    api = FastAPI(title="Instagram assistant", docs_url=None, redoc_url=None, openapi_url=None)
    st = a.store

    @api.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                                   "style-src 'self' 'unsafe-inline'; img-src 'self' data:")
        resp.headers["X-Frame-Options"] = "DENY"
        return resp

    def auth(x_agent_token: str = Header(default="")):
        if not hmac.compare_digest(x_agent_token, token):
            raise HTTPException(401, "missing or wrong token")

    @api.get("/", response_class=HTMLResponse)
    def index():
        return (WEB / "app.html").read_text()

    @api.get("/api/state", dependencies=[Depends(auth)])
    def state():
        c = lambda sql, *p: st.scalar(sql, p)
        spend = st.get("ai_spend", {}) or {}
        return {
            "version": __version__, "you": a.s.account_username, "last_import": st.get("last_import"),
            "job": a.job, "sender": {"active": a.sender.active, "status": a.sender.status,
                                     "waiting_until": a.sender.wait_until if a.sender.wait_until > time.time() else None},
            "counts": {
                "replies_to_check": c("SELECT COUNT(*) FROM drafts WHERE kind = 'reply' AND status = 'draft'"),
                "replies_need_look": c("SELECT COUNT(*) FROM drafts WHERE kind = 'reply' AND status = 'draft' AND "
                                       "(json_extract(context, '$.needs_look') OR json_extract(context, '$.manual'))"),
                "hellos_to_check": c("SELECT COUNT(*) FROM drafts WHERE kind = 'hello' AND status = 'draft'"),
                "approved": c("SELECT COUNT(*) FROM drafts WHERE status = 'approved'"),
                "sending": c("SELECT COUNT(*) FROM drafts WHERE status = 'sending'"),
                "sent_today": st.sent_today(), "sent_total": c("SELECT COUNT(*) FROM drafts WHERE status = 'sent'"),
                "unsure": c("SELECT COUNT(*) FROM drafts WHERE status = 'unsure'"),
                "unfollow_queued": c("SELECT COUNT(*) FROM unfollows WHERE status = 'queued'"),
                "unfollowed_today": st.unfollowed_today(),
                "unfollowed_total": c("SELECT COUNT(*) FROM unfollows WHERE status = 'done'"),
                "following": c("SELECT COUNT(*) FROM people WHERE i_follow = 1"),
                "followers": c("SELECT COUNT(*) FROM people WHERE follows_me = 1"),
                "interesting": c("SELECT COUNT(*) FROM people WHERE interest >= 3"),
                "concerning": c("SELECT COUNT(*) FROM people WHERE concern = 'high'"),
            },
            "settings": {**a.s.pace.model_dump(), "reply_window_days": a.s.reply_window_days,
                         "tuck_window": a.s.browser.start_minimized},
            "ai": {"on": not a.manual, "model": a.s.llm.model, "spend_total": spend.get("total", 0.0),
                   "spend_today": spend.get(now_iso()[:10], 0.0), "drafts": spend.get("drafts", 0)},
            "activity": st.all("SELECT at, kind, text FROM activity ORDER BY id DESC LIMIT 60"),
        }

    # -------------------------------------------------- downloads and AI
    @api.post("/api/import", dependencies=[Depends(auth)])
    def import_download(body: ImportBody):
        paths = [Path(body.path).expanduser()] if body.path else None
        if paths is None and not find_downloads(a.s.downloads_dir):
            raise HTTPException(404, "No Instagram download in your Downloads folder yet. Look for a file starting "
                                     "with instagram- after Instagram emails you.")
        if not a.load_download(paths):
            raise HTTPException(409, "Already busy with something; wait for it to finish.")
        return {"ok": True}

    @api.post("/api/think", dependencies=[Depends(auth)])
    def write_replies():
        if not a.write_replies():
            raise HTTPException(409, "Already busy with something; wait for it to finish.")
        return {"ok": True}

    @api.post("/api/job/stop", dependencies=[Depends(auth)])
    def stop_job():
        a.stop_job()
        return {"ok": True}

    # -------------------------------------------------- drafts
    @api.get("/api/drafts", dependencies=[Depends(auth)])
    def drafts(kind: str = "reply", status: str = "draft", only: str = "", offset: int = 0, limit: int = 100):
        """only=look: the ones that need a proper look; only=interesting: people rated 2 or 3, best first."""
        extra, order = "", "d.id"
        if only == "look":
            extra = (" AND (COALESCE(json_extract(d.context, '$.needs_look'), 0) OR COALESCE(json_extract(d.context, '$.manual'), 0) "
                     "OR d.note IS NOT NULL)")
        elif only == "interesting":
            extra, order = " AND COALESCE(json_extract(d.context, '$.interest'), 0) >= 2", "json_extract(d.context, '$.interest') DESC, d.id"
        statuses = ("unsure", "failed", "skipped") if status == "other" else (status,)
        marks = ",".join("?" * len(statuses))
        rows = st.all(f"""SELECT d.*, p.summary, p.interest, p.concern, p.concern_why, p.note AS person_note
            FROM drafts d LEFT JOIN people p ON p.username = d.username
            WHERE d.kind = ? AND d.status IN ({marks}){extra} ORDER BY {order} LIMIT ? OFFSET ?""",
                      (kind, *statuses, min(limit, 500), max(offset, 0)))
        total = st.scalar(f"SELECT COUNT(*) FROM drafts d WHERE d.kind = ? AND d.status IN ({marks}){extra}", (kind, *statuses))
        for r in rows:
            r["context"] = json.loads(r["context"] or "{}")
            if r["thread_id"]:
                r["recent"] = list(reversed(st.all("SELECT from_me, text, at FROM messages WHERE thread_id = ? ORDER BY at DESC LIMIT 6",
                                                   (r["thread_id"],))))
        return {"total": total, "items": rows}

    def _change(draft_id: int, action: str, text: str | None) -> None:
        d = st.one("SELECT * FROM drafts WHERE id = ?", (draft_id,))
        if d is None:
            raise HTTPException(404, "no such message")
        if d["status"] in ("sending", "sent"):
            raise HTTPException(409, "that one has already gone (or is going) out")
        new_text = d["text"] if text is None else text.strip()
        if action in ("approve", "save") and text is not None and (problems := policy_problems(new_text)):
            raise HTTPException(400, "That message breaks your rules: " + ", ".join(problems))
        status = {"approve": "approved", "unapprove": "draft", "skip": "skipped", "restore": "draft", "save": d["status"]}.get(action)
        if status is None:
            raise HTTPException(400, "unknown action")
        if status == "approved" and not new_text:
            raise HTTPException(400, "Write something first")
        st.execute("UPDATE drafts SET text = ?, status = ?, updated_at = ?, note = CASE WHEN ? = 'approved' THEN NULL ELSE note END "
                   "WHERE id = ?", (new_text, status, now_iso(), status, draft_id))

    @api.post("/api/drafts/{draft_id}", dependencies=[Depends(auth)])
    def change(draft_id: int, body: TextBody):
        _change(draft_id, body.action, body.text)
        return {"ok": True}

    @api.post("/api/drafts", dependencies=[Depends(auth)])
    def bulk(body: BulkBody):
        done, refused = 0, 0
        for i in body.ids:
            try:
                _change(i, body.action, None)
                done += 1
            except HTTPException:
                refused += 1
        return {"done": done, "refused": refused}

    @api.post("/api/drafts/all/{kind}", dependencies=[Depends(auth)])
    def approve_all(kind: str):
        """Approve every ready draft of a kind. Replies that need a proper look, or have no text yet, are left for
        Luke to do one by one."""
        if kind not in ("hello", "reply"):
            raise HTTPException(404, "unknown")
        n = st.execute("""UPDATE drafts SET status = 'approved', updated_at = ? WHERE kind = ? AND status = 'draft' AND text != ''
            AND NOT COALESCE(json_extract(context, '$.needs_look'), 0) AND NOT COALESCE(json_extract(context, '$.manual'), 0)""",
                       (now_iso(), kind)).rowcount
        st.log("info", f"Approved {n} {'hellos' if kind == 'hello' else 'replies'}.")
        return {"approved": n}

    # -------------------------------------------------- sending
    @api.post("/api/send/{what}", dependencies=[Depends(auth)])
    def send(what: str):
        if what == "start":
            a.sender.go()
            st.log("info", "Started sending what you approved.")
        elif what == "pause":
            a.sender.pause("Paused by you")
            st.log("info", "Paused.")
        else:
            raise HTTPException(404, "unknown")
        return {"ok": True}

    # -------------------------------------------------- people
    @api.get("/api/people", dependencies=[Depends(auth)])
    def people(q: str = "", show: str = "following", sort: str = "recent", offset: int = 0, limit: int = 50):
        where = {"following": "p.i_follow = 1", "followers": "p.follows_me = 1", "chats": "EXISTS (SELECT 1 FROM threads t WHERE t.username = p.username)",
                 "interesting": "p.interest >= 2", "concerning": "p.concern IN ('high', 'low')", "all": "1"}.get(show, "p.i_follow = 1")
        order = {"recent": "p.followed_at DESC, p.username", "interest": "COALESCE(p.interest, -1) DESC, p.username",
                 "name": "p.username"}.get(sort, "p.username")
        like = f"%{q.strip().lstrip('@').lower()}%"
        sql = f"""FROM people p LEFT JOIN unfollows u ON u.username = p.username
                  LEFT JOIN drafts h ON h.kind = 'hello' AND h.username = p.username
                  WHERE {where} AND (p.username LIKE ? OR LOWER(COALESCE(p.display_name, '')) LIKE ?)"""
        total = st.scalar("SELECT COUNT(*) " + sql, (like, like))
        items = st.all(f"""SELECT p.username, p.display_name, p.i_follow, p.follows_me, p.summary, p.interest, p.interest_why,
            p.concern, p.concern_why, p.note, p.memory, u.status AS unfollow, h.status AS hello {sql}
            ORDER BY {order} LIMIT ? OFFSET ?""", (like, like, min(limit, 200), max(offset, 0)))
        for it in items:
            it["memory"] = json.loads(it["memory"] or "[]")
        return {"total": total, "items": items}

    @api.post("/api/people/{username}/note", dependencies=[Depends(auth)])
    def note(username: str, body: NoteBody):
        st.execute("UPDATE people SET note = ? WHERE username = ?", (body.note.strip()[:500] or None, username.lower()))
        return {"ok": True}

    @api.post("/api/unfollow", dependencies=[Depends(auth)])
    def unfollow(body: UnfollowBody):
        if body.everyone:
            if body.confirm.strip() != "UNFOLLOW EVERYONE":
                raise HTTPException(400, "Type UNFOLLOW EVERYONE to confirm")
            names = [r["username"] for r in st.all("SELECT username FROM people WHERE i_follow = 1")]
        else:
            names = [n.lstrip("@").lower() for n in body.usernames]
        queued = 0
        for n in names:
            cur = st.execute("INSERT OR IGNORE INTO unfollows(username, queued_at) VALUES (?, ?)", (n, now_iso()))
            queued += cur.rowcount
            # A hello to someone you're unfollowing makes no sense: drop it if it hasn't gone.
            st.execute("UPDATE drafts SET status = 'skipped', note = 'you unfollowed them' WHERE kind = 'hello' "
                       "AND username = ? AND status IN ('draft', 'approved')", (n,))
        st.log("info", f"Added {queued} people to the unfollow list.")
        return {"queued": queued}

    @api.post("/api/unfollow/cancel", dependencies=[Depends(auth)])
    def cancel_unfollow():
        n = st.execute("DELETE FROM unfollows WHERE status = 'queued'").rowcount
        st.log("info", f"Cancelled {n} unfollows that hadn't happened yet.")
        return {"cancelled": n}

    # -------------------------------------------------- about me
    @api.get("/api/about", dependencies=[Depends(auth)])
    def about():
        p = a.profile()
        never = a.s.never_list_path.read_text() if a.s.never_list_path.exists() else ""
        return {"facts": [{"id": f.id, "text": f.text, "private": not f.shareable} for f in p.facts],
                "hellos": p.intro_templates, "voice": p.voice, "never": never}

    @api.post("/api/about", dependencies=[Depends(auth)])
    def save_about(body: AboutBody):
        p = a.profile()
        old = {f.id: f for f in p.facts}
        facts, n = [], 0
        for f in body.facts:
            text = (f.get("text") or "").strip()
            if not text:
                continue
            fid = f.get("id") if f.get("id") in old else None
            if fid is None:
                n += 1
                fid = f"f_luke_{int(time.time())}_{n}"
            base = old.get(fid)
            facts.append(Fact(id=fid, text=text[:300], topic=(base.topic if base and base.text == text else text.lower()),
                              source="user", shareable=not f.get("private")))
        hellos = [h.strip() for h in body.hellos if h.strip()]
        if bad := [h for h in hellos if policy_problems(h)]:
            raise HTTPException(400, f"This hello breaks your rules: {bad[0]}")
        p.facts, p.intro_templates, p.voice = facts, hellos, body.voice.strip()
        p.save(a.s.profile_path)
        a.s.never_list_path.write_text(body.never.strip() + "\n")
        st.log("info", "Saved your About me.")
        return {"ok": True}

    # -------------------------------------------------- settings
    @api.post("/api/settings", dependencies=[Depends(auth)])
    def settings(body: SettingsBody):
        env, pace = {}, a.s.pace
        for field, key in (("daily_messages", "AGENT_PACE__DAILY_MESSAGES"), ("daily_unfollows", "AGENT_PACE__DAILY_UNFOLLOWS"),
                           ("message_gap_s", "AGENT_PACE__MESSAGE_GAP_S"), ("unfollow_gap_s", "AGENT_PACE__UNFOLLOW_GAP_S")):
            v = getattr(body, field)
            if v is not None:
                limit = 300 if "daily" in field else 600
                if not 0 <= v <= limit:
                    raise HTTPException(400, f"{field} must be between 0 and {limit}")
                setattr(pace, field, v)
                env[key] = str(v)
        if body.reply_window_days is not None:
            a.s.reply_window_days = max(1, body.reply_window_days)
            env["AGENT_REPLY_WINDOW_DAYS"] = str(a.s.reply_window_days)
        if body.tuck_window is not None:
            a.s.browser.start_minimized = body.tuck_window
            env["AGENT_BROWSER__START_MINIMIZED"] = "true" if body.tuck_window else "false"
            web = a.sender.web
            if web is not None:
                web.sup.window_request = "minimized" if body.tuck_window else "maximized"
        if body.ai_key is not None:
            key = body.ai_key.strip()
            if not re.fullmatch(r"sk-ant-[A-Za-z0-9_\-]{20,}", key):
                raise HTTPException(400, "That doesn't look like an Anthropic API key (it starts with sk-ant-api03-).")
            a.s.llm.provider, a.s.llm.api_key = "anthropic", SecretStr(key)
            a.llm = build_provider(a.s.llm)
            env.update({"AGENT_LLM__PROVIDER": "anthropic", "AGENT_LLM__API_KEY": key})
            st.log("good", "AI replies are on.")
        if env:
            save_env(env)
        return {"ok": True}

    return api
