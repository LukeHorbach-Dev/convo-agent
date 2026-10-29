"""Ties the pieces together: the store, the AI, the sender, and the jobs the dashboard starts (load a download,
write replies, the self-check). Also the dashboard's web API."""
from __future__ import annotations

import base64
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, SecretStr

from agent.intel.llm import build_provider
from agent.intel.profile import Fact, Profile
from agent.intel.reasoning import POLICY_VIOLATIONS
from agent.notify import mac_notify

from . import __version__
from .ask import Ask, queue_message
from .brain import never_list, prepare_hellos, think
from .config import AssistantSettings
from .export import ExportError, apply_export, describe_export, find_downloads, read_export
from .reports import briefing, people_csv, person, write_report
from .sender import Sender
from .store import Store, now_iso
from .writer import FakeBatches, Overnight, RealBatches, estimate_usd

WEB = Path(__file__).parent / "web"
WRITERS = ("claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5")
PRACTICE_PORT = 8766


def save_env(values: dict[str, str | None], path: Path = Path(".env")) -> None:
    """Set keys in the local .env (None removes one), kept owner-only because it can hold the API key."""
    lines = [l for l in (path.read_text().splitlines() if path.exists() else []) if l.split("=", 1)[0].strip() not in values]
    path.write_text("\n".join(lines + [f"{k}={v}" for k, v in values.items() if v is not None]) + "\n")
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


def policy_problems(text: str) -> list[str]:
    return [name for rx, name in POLICY_VIOLATIONS if rx.search(text)]


class Assistant:
    def __init__(self, settings: AssistantSettings, store: Store | None = None, make_web=None, llm=None, notify=mac_notify,
                 practice=None, env_path: Path = Path(".env")):
        self.s = settings
        self.store = store or Store(settings.db_path)
        self.notify = notify
        self.practice = practice                  # practice.World holder in practice mode, else None
        self.env_path = env_path
        self.llm = llm or build_provider(settings.llm)
        self.make_web = make_web or self._instagram
        self.sender = Sender(self.store, settings, self.make_web, notify=notify)
        self.overnight = self._overnight()
        self.ask = Ask(self, client_factory=lambda: getattr(self.llm, "client", None))
        self.job = {"name": None, "done": 0, "total": 0, "error": None, "started": None}
        self.live = {"png": None, "note": "", "at": 0.0}      # the latest page Claude saw, for the live view
        self.check = {"running": False, "report": None, "at": None}
        self.auto_send = False                    # practice mode only: approve and send as soon as replies are written
        self.on_quit = None
        self._job_lock = threading.Lock()
        self._stop_job = threading.Event()
        self._bg_stop = threading.Event()

    # ------------------------------------------------------------------ the AI and the website
    @property
    def manual(self) -> bool:
        return getattr(self.llm, "name", "") == "fake" and self.practice is None

    def _overnight(self) -> Overnight:
        batches = RealBatches(self.llm) if hasattr(self.llm, "client") else FakeBatches(self.llm)
        return Overnight(self.store, self.llm, batches)

    def rebuild_ai(self) -> None:
        """After a new key or writer model: new AI client, and the browser reopens with it before the next send."""
        if self.practice is None:
            self.llm = build_provider(self.s.llm)
            self.overnight = self._overnight()
        self.ask.reset()
        self.sender.reset_browser()

    def _instagram(self, site=None):
        """The website, used through Claude's eyes (eyes.Hands). Without an AI key the browser still opens (for signing
        in), but anything that needs Claude stops with a plain explanation."""
        from agent.platform.browser import BrowserSupervisor
        from agent.platform.sites import INSTAGRAM
        from .eyes import Hands
        client = None
        if self.s.llm.provider == "anthropic" and self.s.llm.api_key:
            import anthropic
            client = anthropic.Anthropic(api_key=self.s.llm.api_key.get_secret_value(), timeout=60, max_retries=2)
        site = site or INSTAGRAM
        hands = Hands(BrowserSupervisor(self.s.browser, site), site, self.s.account_username, client,
                      model=self.s.eyes.model, effort=self.s.eyes.effort,
                      on_cost=lambda usd: self.store.add_spend(usd, "web"), on_view=self._view)
        hands.interrupt = self.sender.halt.is_set
        return hands

    def _view(self, png: bytes, note: str) -> None:
        self.live.update(png=png, note=note, at=time.time())

    def profile(self) -> Profile:
        return Profile.load(self.s.profile_path)

    def never(self) -> set[str]:
        return never_list(self.s.never_list_path)

    # ------------------------------------------------------------------ background
    def start_background(self) -> None:
        self.sender.start()
        threading.Thread(target=self._background, name="overnight", daemon=True).start()

    def _background(self) -> None:
        while not self._bg_stop.wait(60):
            try:
                self.poll_overnight()
            except Exception as e:                  # never let the thread die
                self.store.log("warn", f"Couldn't check the overnight replies yet ({e}); trying again in a minute.")

    def shutdown(self) -> None:
        self._bg_stop.set()
        self.stop_job()
        self.sender.shutdown()

    def poll_overnight(self) -> str:
        if not self.overnight.pending:
            return "none"
        status = self.overnight.check(self.profile())
        if status == "done":
            self._after_writing()
        return status

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

    def load_download(self, paths: list[Path] | None = None, then_write: bool = True) -> bool:
        def work():
            if self.practice is not None and not paths:
                self.practice.new_download(self.s.downloads_dir)
            files = paths or find_downloads(self.s.downloads_dir)
            if not files:
                raise ExportError("no Instagram download found in Downloads (a file starting with instagram-)")
            self.store.log("info", "Loading " + ", ".join(Path(f).name for f in files) + " ...")
            data = read_export([Path(f) for f in files])
            apply_export(self.store, data, self.s.account_username)
            prepare_hellos(self.store, self.profile(), self.never())
            if then_write:
                self.job["name"] = "writing replies"
                self._write(now=False)
        return self._run_job("loading your download", work)

    def write_replies(self, now: bool = True) -> bool:
        return self._run_job("writing replies", lambda: self._write(now=now))

    def _write(self, now: bool) -> None:
        if self.over_budget():
            self.store.log("warn", self._budget_words("writing replies"))
            return
        if self.manual:
            think(self.store, self.profile(), self.llm, self.never(), window_days=self.s.reply_window_days, manual=True,
                  min_age=self.s.min_age)
            return
        if now and self.overnight.pending:
            self.overnight.cancel()              # write those straight away too
        if self.s.write_overnight and not now:
            out = self.overnight.submit(self.profile(), self.never(), window_days=self.s.reply_window_days,
                                        min_age=self.s.min_age)
            if out["queued"] and isinstance(self.overnight.batches, FakeBatches):
                self.poll_overnight()             # the free stand-in answers at once
            elif not out["queued"]:
                self._after_writing()
            return
        def progress(done, total):
            self.job["done"], self.job["total"] = done, total
        think(self.store, self.profile(), self.llm, self.never(), window_days=self.s.reply_window_days,
              min_age=self.s.min_age, progress=progress, stop=lambda: self._stop_job.is_set() or self.over_budget())
        if self.over_budget():
            self.store.log("warn", self._budget_words("writing replies"))
        self._after_writing()

    def _after_writing(self) -> None:
        ready = self.store.scalar("SELECT COUNT(*) FROM drafts WHERE status = 'draft'")
        if self.practice is not None and self.auto_send:
            n = self.approve_ready("reply") + self.approve_ready("hello")
            if n:
                self.store.log("info", f"Automatic (practice): approved {n} and started sending.")
                self.sender.go()
            return
        if ready:
            self.notify("Instagram assistant", f"{ready} messages ready for you to check")

    def approve_ready(self, kind: str) -> int:
        """Approve every ready draft of a kind. Replies that need a proper look, or have no text yet, are left for
        Luke to do one by one."""
        n = self.store.execute("""UPDATE drafts SET status = 'approved', updated_at = ? WHERE kind = ? AND status = 'draft' AND text != ''
            AND NOT COALESCE(json_extract(context, '$.needs_look'), 0) AND NOT COALESCE(json_extract(context, '$.manual'), 0)""",
                               (now_iso(), kind)).rowcount
        if n:
            self.store.log("info", f"Approved {n} {'hellos' if kind == 'hello' else 'replies'}.")
        return n

    def over_budget(self) -> bool:
        return not self.manual and self.store.spent_today() >= self.s.daily_ai_budget_usd

    def _budget_words(self, what: str) -> str:
        return (f"Stopped {what}: reached today's AI spending limit (${self.s.daily_ai_budget_usd:.2f}). "
                "It carries on tomorrow, or raise the limit in Settings.")

    def stop_job(self) -> None:
        self._stop_job.set()

    def stop_everything(self) -> None:
        """The Stop now button: sending stops at Claude's next click, writing stops after the reply it's on."""
        self.sender.stop_now()
        self._stop_job.set()
        self.store.log("warn", "Stopped everything (you pressed Stop now).")

    # ------------------------------------------------------------------ browser jobs (run on the sender's thread)
    def run_check(self) -> bool:
        from .check import run_check
        if self.check["running"]:
            return False
        self.check = {"running": True, "report": None, "at": now_iso()}

        def work():
            try:
                report = self.sender.run_on_browser(lambda web: run_check(web, self.store)).result(timeout=900)
            except Exception as e:
                report = f"Self-check: couldn't run ({type(e).__name__}: {str(e).splitlines()[0][:200]})"
            out = self.s.data_dir / "diagnostics" / "check.txt"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(report + "\n")
            self.check = {"running": False, "report": report, "at": now_iso()}
            self.store.log("good" if report.startswith("Self-check: 6 of 6") else "warn", report.splitlines()[0])
        threading.Thread(target=work, name="check", daemon=True).start()
        return True


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


class NeverBody(BaseModel):
    on: bool


class SendBody(BaseModel):
    just_one: bool = False


class ThinkBody(BaseModel):
    now: bool = True


class ImportBody(BaseModel):
    path: str | None = None


class AskBody(BaseModel):
    text: str


class PracticeBody(BaseModel):
    real_ai: bool = False


class MessageBody(BaseModel):
    username: str
    text: str
    approve: bool = False


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
    eyes_model: str | None = None
    writer_model: str | None = None
    write_overnight: bool | None = None
    daily_ai_budget_usd: float | None = None
    send_at: str | None = None       # "HH:MM", or "" for off
    quiet_from: str | None = None
    quiet_to: str | None = None
    auto_send: bool | None = None    # practice mode only


HHMM = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]$")


def create_api(a: Assistant, token: str) -> FastAPI:
    api = FastAPI(title="Convo", docs_url=None, redoc_url=None, openapi_url=None)
    st = a.store

    @api.middleware("http")
    async def guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ("127.0.0.1", "localhost"):
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
                                                   "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
                                                   "connect-src 'self'")
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "no-referrer"
        return resp

    def auth(x_agent_token: str = Header(default="")):
        if not hmac.compare_digest(x_agent_token, token):
            raise HTTPException(401, "missing or wrong token")

    @api.get("/", response_class=HTMLResponse)
    def index():
        return (WEB / "app.html").read_text()

    @api.get("/api/health")
    def health():
        return {"app": "convo", "version": __version__, "practice": a.practice is not None,
                "practice_ai": bool(getattr(a, "practice_ai", False))}

    @api.post("/api/quit", dependencies=[Depends(auth)])
    def quit_app():
        st.log("info", "Quitting.")
        if a.on_quit:
            threading.Timer(0.3, a.on_quit).start()
        return {"ok": True}

    # -------------------------------------------------- state
    @api.get("/api/state", dependencies=[Depends(auth)])
    def state():
        c = lambda sql, *p: st.scalar(sql, p)
        spend = st.get("ai_spend", {}) or {}
        pending = a.overnight.pending
        model = a.s.llm.model
        return {
            "version": __version__, "you": a.s.account_username, "last_import": st.get("last_import"),
            "practice": a.practice is not None, "auto_send": a.auto_send,
            "job": a.job, "check": {"running": a.check["running"], "at": a.check["at"],
                                    "summary": (a.check["report"] or "").split("\n\n== ")[0] or None},
            "sender": {"active": a.sender.active, "status": a.sender.status, "stopped": a.sender.halt.is_set(),
                       "waiting_until": a.sender.wait_until if a.sender.wait_until > time.time() else None,
                       "quiet_until": a.sender.quiet_until()},
            "overnight": {"count": len(pending["jobs"]), "since": pending["at"]} if pending else None,
            "counts": {
                "replies_to_check": c("SELECT COUNT(*) FROM drafts WHERE kind = 'reply' AND status = 'draft'"),
                "replies_need_look": c("SELECT COUNT(*) FROM drafts WHERE kind = 'reply' AND status = 'draft' AND "
                                       "(json_extract(context, '$.needs_look') OR json_extract(context, '$.manual'))"),
                "hellos_to_check": c("SELECT COUNT(*) FROM drafts WHERE kind = 'hello' AND status = 'draft'"),
                "writing": c("SELECT COUNT(*) FROM drafts WHERE status = 'writing'"),
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
            "briefing": briefing(st),
            "settings": {**a.s.pace.model_dump(), "reply_window_days": a.s.reply_window_days,
                         "tuck_window": a.s.browser.start_minimized, "eyes_model": a.s.eyes.model,
                         "writer_model": model, "write_overnight": a.s.write_overnight,
                         "daily_ai_budget_usd": a.s.daily_ai_budget_usd, "send_at": a.s.send_at or "",
                         "quiet_from": a.s.quiet_from or "", "quiet_to": a.s.quiet_to or ""},
            "ai": {"on": not a.manual, "model": model, "spend_total": spend.get("total", 0.0),
                   "spend_today": st.spent_today(), "drafts": spend.get("drafts", 0),
                   "drafts_usd": spend.get("drafts_usd", 0.0), "web_steps": spend.get("web", 0),
                   "web_usd": spend.get("web_usd", 0.0), "eyes_model": a.s.eyes.model,
                   "other_usd": spend.get("reports_usd", 0.0) + spend.get("ask_usd", 0.0),
                   "estimates": {m: {"overnight": estimate_usd(m, True), "now": estimate_usd(m, False)} for m in WRITERS},
                   "send_estimate": 0.01 if a.s.eyes.model.startswith("claude-haiku") else 0.15},
            "activity": st.all("SELECT at, kind, text FROM activity ORDER BY id DESC LIMIT 80"),
        }

    # -------------------------------------------------- downloads and AI
    @api.post("/api/import", dependencies=[Depends(auth)])
    def import_download(body: ImportBody):
        paths = [Path(body.path).expanduser()] if body.path else None
        if paths is None and a.practice is None and not find_downloads(a.s.downloads_dir):
            raise HTTPException(404, "No Instagram download in your Downloads folder yet. Look for a file starting "
                                     "with instagram- after Instagram emails you.")
        if not a.load_download(paths):
            raise HTTPException(409, "Already busy with something; wait for it to finish.")
        return {"ok": True}

    @api.post("/api/download/report", dependencies=[Depends(auth)])
    def download_report():
        files = find_downloads(a.s.downloads_dir)
        if not files:
            raise HTTPException(404, "No Instagram download in your Downloads folder.")
        try:
            report = describe_export(files)
        except Exception as e:
            raise HTTPException(400, f"Couldn't read the download ({str(e).splitlines()[0][:160]})")
        out = a.s.data_dir / "diagnostics" / "download-check.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report + "\n")
        return {"report": report}

    @api.post("/api/think", dependencies=[Depends(auth)])
    def write_replies(body: ThinkBody | None = None):
        if not a.write_replies(now=body.now if body else True):
            raise HTTPException(409, "Already busy with something; wait for it to finish.")
        return {"ok": True}

    @api.post("/api/overnight/check", dependencies=[Depends(auth)])
    def overnight_check():
        return {"status": a.poll_overnight()}

    @api.post("/api/job/stop", dependencies=[Depends(auth)])
    def stop_job():
        a.stop_job()
        return {"ok": True}

    @api.post("/api/stop", dependencies=[Depends(auth)])
    def stop_now():
        a.stop_everything()
        return {"ok": True}

    # -------------------------------------------------- ask
    @api.post("/api/ask", dependencies=[Depends(auth)])
    def ask(body: AskBody):
        try:
            return a.ask.run(body.text)
        except Exception as e:
            a.ask.reset()
            raise HTTPException(502, f"The Ask box couldn't do that ({str(e).splitlines()[0][:160]}). Try again.")

    @api.post("/api/ask/reset", dependencies=[Depends(auth)])
    def ask_reset():
        a.ask.reset()
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
        if d["status"] == "writing":
            raise HTTPException(409, "that one is still being written")
        new_text = d["text"] if text is None else text.strip()
        if action in ("approve", "save") and text is not None and (problems := policy_problems(new_text)):
            raise HTTPException(400, "That message breaks your rules: " + ", ".join(problems))
        status = {"approve": "approved", "unapprove": "draft", "skip": "skipped", "restore": "draft", "save": d["status"]}.get(action)
        if status is None:
            raise HTTPException(400, "unknown action")
        if status == "approved" and not new_text:
            raise HTTPException(400, "Write something first")
        if action == "save":                        # only the words: an approve arriving at the same moment stays
            st.execute("UPDATE drafts SET text = ?, updated_at = ? WHERE id = ? AND status NOT IN ('sending', 'sent')",
                       (new_text, now_iso(), draft_id))
            return
        st.execute("UPDATE drafts SET text = ?, status = ?, updated_at = ?, note = CASE WHEN ? = 'approved' THEN NULL ELSE note END "
                   "WHERE id = ? AND status NOT IN ('sending', 'sent')", (new_text, status, now_iso(), status, draft_id))

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
        if kind not in ("hello", "reply"):
            raise HTTPException(404, "unknown")
        return {"approved": a.approve_ready(kind)}

    @api.post("/api/message", dependencies=[Depends(auth)])
    def new_message(body: MessageBody):
        """A message Luke writes himself, to anyone (from a person's panel)."""
        result = queue_message(st, a.never(), body.username, body.text, body.approve)
        if result.startswith(("Queued", "Saved")):
            return {"ok": True, "detail": result}
        raise HTTPException(400, result)

    # -------------------------------------------------- sending
    @api.post("/api/send/{what}", dependencies=[Depends(auth)])
    def send(what: str, body: SendBody | None = None):
        if what == "start":
            one = bool(body and body.just_one)
            a.sender.go(just_one=one)
            st.log("info", "Sending one message so you can watch it." if one else "Started sending what you approved.")
        elif what == "pause":
            a.sender.pause("Paused by you")
            st.log("info", "Paused.")
        else:
            raise HTTPException(404, "unknown")
        return {"ok": True}

    @api.get("/api/live", dependencies=[Depends(auth)])
    def live():
        """The latest page Claude looked at, while sending or unfollowing (stays on this Mac)."""
        lv = a.live
        fresh = lv["png"] is not None and time.time() - lv["at"] < 120
        return {"active": a.sender.active, "status": a.sender.status, "note": lv["note"] if fresh else "",
                "image": base64.b64encode(lv["png"]).decode() if fresh else None}

    # -------------------------------------------------- Instagram sign-in and the self-check
    @api.post("/api/instagram/login", dependencies=[Depends(auth)])
    def instagram_login():
        try:
            a.sender.run_on_browser(lambda web: web.open_login()).result(timeout=60)
        except Exception as e:
            raise HTTPException(502, f"Couldn't open the sign-in page ({str(e).splitlines()[0][:160]})")
        return {"ok": True}

    @api.post("/api/instagram/login/done", dependencies=[Depends(auth)])
    def instagram_login_done():
        try:
            ok = a.sender.run_on_browser(lambda web: web.looks_signed_in()).result(timeout=90)
        except Exception as e:
            raise HTTPException(502, f"Couldn't check ({str(e).splitlines()[0][:160]})")
        a.sender.identity_ok = False
        st.log("good" if ok else "warn", "Signed in to Instagram." if ok else "Instagram still shows the sign-in page.")
        return {"signed_in": ok}

    @api.post("/api/check", dependencies=[Depends(auth)])
    def start_check():
        if a.sender.active:
            raise HTTPException(409, "Pause sending first, then run the check.")
        if not a.run_check():
            raise HTTPException(409, "The check is already running.")
        return {"ok": True}

    @api.get("/api/check", dependencies=[Depends(auth)])
    def check_result():
        return a.check

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

    @api.get("/api/people.csv", dependencies=[Depends(auth)])
    def people_spreadsheet():
        return PlainTextResponse(people_csv(st), media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="convo-people.csv"'})

    @api.get("/api/people/{username}", dependencies=[Depends(auth)])
    def one_person(username: str):
        p = person(st, username, a.never())
        if p is None:
            raise HTTPException(404, f"@{username} isn't in your downloads")
        return p

    @api.post("/api/people/{username}/report", dependencies=[Depends(auth)])
    def report(username: str):
        if a.manual:
            raise HTTPException(400, "Reports need your AI key (Settings).")
        if a.over_budget():
            raise HTTPException(400, a._budget_words("reports"))
        try:
            return write_report(st, a.llm, a.profile(), username, a.never())
        except LookupError as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            raise HTTPException(502, f"Couldn't write the report ({str(e).splitlines()[0][:160]})")

    @api.post("/api/people/{username}/note", dependencies=[Depends(auth)])
    def note(username: str, body: NoteBody):
        u = username.lower()
        st.execute("INSERT OR IGNORE INTO people(username) VALUES (?)", (u,))
        st.execute("UPDATE people SET note = ? WHERE username = ?", (body.note.strip()[:500] or None, u))
        return {"ok": True}

    @api.post("/api/people/{username}/never", dependencies=[Depends(auth)])
    def never(username: str, body: NeverBody):
        u = username.strip().lstrip("@").lower()
        path = a.s.never_list_path
        lines = [l for l in (path.read_text().splitlines() if path.exists() else []) if l.strip().lstrip("@").lower() != u]
        if body.on:
            lines.append(u)
            st.execute("UPDATE drafts SET status = 'skipped', note = 'on your never list' WHERE username = ? "
                       "AND status IN ('draft', 'approved')", (u,))
        path.write_text("\n".join(l for l in lines if l.strip()) + "\n")
        st.log("info", f"@{u} {'added to' if body.on else 'taken off'} the never list.")
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

    # -------------------------------------------------- practice
    @api.post("/api/practice/start", dependencies=[Depends(auth)])
    def practice_start(body: PracticeBody | None = None):
        if a.practice is not None:
            raise HTTPException(400, "This is already the practice copy.")
        from .window import ask_to_quit, health, wait_until
        real_ai = bool(body and body.real_ai)
        if real_ai and a.manual:
            raise HTTPException(400, "Practice with real Claude needs your AI key (Settings).")
        folder = a.s.data_dir / "practice"
        folder.mkdir(parents=True, exist_ok=True)
        running = health(PRACTICE_PORT)
        if running and running.get("practice_ai") != real_ai:        # restart it the other way
            ask_to_quit(PRACTICE_PORT, (folder / "dashboard-token").read_text().strip())
            wait_until(PRACTICE_PORT, up=False, timeout_s=20)
            running = None
        if not running:
            log = open(folder / "practice.log", "ab")
            subprocess.Popen([sys.executable, "-m", "assistant", "practice"] + (["--real-ai"] if real_ai else []),
                             cwd=os.getcwd(), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                             start_new_session=True, env=os.environ.copy())
            if not wait_until(PRACTICE_PORT, up=True, timeout_s=60):
                raise HTTPException(502, "Practice didn't start. Details are in data/practice/practice.log.")
        tok = (folder / "dashboard-token").read_text().strip()
        return {"url": f"http://127.0.0.1:{PRACTICE_PORT}/#token={tok}"}

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
            if web is not None and hasattr(web, "sup"):
                web.sup.window_request = "minimized" if body.tuck_window else "maximized"
        if body.ai_key is not None:
            key = body.ai_key.strip()
            if not re.fullmatch(r"sk-ant-[A-Za-z0-9_\-]{20,}", key):
                raise HTTPException(400, "That doesn't look like an Anthropic API key (it starts with sk-ant-api03-).")
            a.s.llm.provider, a.s.llm.api_key = "anthropic", SecretStr(key)
            env.update({"AGENT_LLM__PROVIDER": "anthropic", "AGENT_LLM__API_KEY": key})
            a.rebuild_ai()
            st.log("good", "AI is on.")
        if body.writer_model is not None:
            if body.writer_model not in WRITERS:
                raise HTTPException(400, "writer_model must be one of " + ", ".join(WRITERS))
            a.s.llm.model = body.writer_model
            env["AGENT_LLM__MODEL"] = body.writer_model
            a.rebuild_ai()
        if body.write_overnight is not None:
            a.s.write_overnight = body.write_overnight
            env["AGENT_WRITE_OVERNIGHT"] = "true" if body.write_overnight else "false"
        if body.daily_ai_budget_usd is not None:
            if not 0.5 <= body.daily_ai_budget_usd <= 100:
                raise HTTPException(400, "The daily AI limit must be between 0.50 and 100 dollars")
            a.s.daily_ai_budget_usd = body.daily_ai_budget_usd
            env["AGENT_DAILY_AI_BUDGET_USD"] = str(body.daily_ai_budget_usd)
        if body.eyes_model is not None:
            if body.eyes_model not in ("claude-haiku-5-5", "claude-sonnet-5-5"):
                raise HTTPException(400, "eyes_model must be claude-haiku-5-5 or claude-sonnet-5-5")
            a.s.eyes.model = body.eyes_model
            env["AGENT_EYES__MODEL"] = body.eyes_model
            a.sender.reset_browser()
        for field, key in (("send_at", "AGENT_SEND_AT"), ("quiet_from", "AGENT_QUIET_FROM"), ("quiet_to", "AGENT_QUIET_TO")):
            v = getattr(body, field)
            if v is None:
                continue
            v = v.strip()
            if v and not HHMM.match(v):
                raise HTTPException(400, f"{field.replace('_', ' ')} must be a time like 09:00")
            setattr(a.s, field, v or None)
            env[key] = v or None
        if body.auto_send is not None:
            if a.practice is None:
                raise HTTPException(400, "Automatic sending is only in practice mode. Real messages wait for your approval.")
            a.auto_send = body.auto_send
            st.log("info", f"Automatic sending (practice) {'on' if body.auto_send else 'off'}.")
        if env:
            save_env(env, a.env_path)
        return {"ok": True}

    return api
