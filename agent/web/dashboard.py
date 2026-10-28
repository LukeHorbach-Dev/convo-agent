"""Local dashboard and operator API.

Security: binds to localhost (enforced by config); every /api call needs the X-Agent-Token header (constant-time
compare). Cross-site requests can't set custom headers without a CORS preflight, which this app never grants,
so state-changing calls are not reachable by CSRF. Host headers other than localhost are rejected to stop DNS
rebinding. The platform/browser is never touched from request threads; health comes from the worker's cache.
"""
from __future__ import annotations

import hmac
import json
import platform as pyplatform
import secrets
import time
from datetime import timedelta
from importlib.metadata import version
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .. import __version__
from ..app import App
from ..core.db import iso, now
from ..core.observability import metrics, redact
from ..core.recovery import RunState

ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]"}


def load_or_create_token(data_dir: Path) -> str:
    path = Path(data_dir) / "dashboard-token"
    if path.exists():
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    path.write_text(token)
    path.chmod(0o600)
    return token


class ApproveAllBody(BaseModel):
    ids: list[str]


class ApproveBody(BaseModel):
    text: str | None = None


class ResolveBody(BaseModel):
    delivered: bool


def rows(db, sql, params=()):
    return [dict(r) for r in db.all(sql, params)]


def create_dashboard(app: App, token: str, scheduler=None) -> FastAPI:
    api = FastAPI(title="Conversation agent", docs_url=None, redoc_url=None, openapi_url=None)
    db, engine = app.db, app.engine

    @api.middleware("http")
    async def host_guard(request: Request, call_next):
        host = (request.headers.get("host") or "").rsplit(":", 1)[0]
        if host not in ALLOWED_HOSTS:
            return JSONResponse({"error": "host not allowed"}, status_code=403)
        resp = await call_next(request)
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Content-Security-Policy"] = "default-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def auth(x_agent_token: str = Header(default="")):
        if not hmac.compare_digest(x_agent_token, token):
            raise HTTPException(401, "missing or wrong X-Agent-Token")

    def wake():
        if scheduler:
            scheduler.wake()

    def platform_health() -> dict:
        if scheduler and getattr(scheduler, "platform_health", None):
            return scheduler.platform_health
        return {"ok": None, "note": "no worker tick yet"}

    @api.get("/", response_class=HTMLResponse)
    def index():
        return HTMLResponse((Path(__file__).parent / "dashboard.html").read_text())

    @api.get("/api/health", dependencies=[Depends(auth)])
    def health():
        try:
            db.scalar("SELECT 1")
            db_ok = {"ok": True}
        except Exception as e:
            db_ok = {"ok": False, "error": str(e)}
        hb = scheduler.heartbeat_at if scheduler else None
        return {"database": db_ok, "platform": platform_health(), "model": app.llm.health(),
                "worker": {"ok": hb is not None and time.time() - hb < max(120, app.settings.poll_interval_s * 3),
                           "seconds_since_tick": None if hb is None else round(time.time() - hb, 1),
                           "last_error": scheduler.last_error if scheduler else None},
                "capabilities": app.platform.capabilities().__dict__}

    def not_contacted() -> list[dict]:
        """Accounts the policy decided not to introduce to, with the reason (from the audit log)."""
        out = []
        for r in db.all("SELECT detail FROM audit WHERE action = 'intro_not_eligible' ORDER BY id DESC LIMIT 50"):
            d = json.loads(r["detail"])
            user = db.scalar("SELECT username FROM accounts WHERE id = ?", (d.get("account"),))
            out.append({"subject_key": user, "state": "not contacted", "reason": d.get("reason")})
        return out

    @api.get("/api/status", dependencies=[Depends(auth)])
    def status():
        c = engine.controls
        return {
            "run_state": c.run_state.value, "pause_reason": c.pause_reason, "emergency_stop": c.emergency_stopped,
            "mode": app.settings.mode.value, "platform": app.settings.platform.value, "platform_is_real": app.platform.is_real,
            "approval_required": app.platform.is_real or app.settings.policy.mock_requires_approval,
            "circuit": engine.circuit.info(), "queue_depth": engine.jobs.depth(),
            "jobs_by_state": {r["state"]: r["n"] for r in db.all("SELECT state, COUNT(*) n FROM jobs GROUP BY state")},
            "active_jobs": rows(db, "SELECT id, kind, subject_key, state, state_since, attempts, error_category, last_error FROM jobs "
                                    "WHERE state NOT IN ('completed','skipped','failed','cancelled') ORDER BY updated_at DESC LIMIT 50"),
            "drafts": rows(db, "SELECT d.id, d.target, d.text, d.intent, d.rationale, d.facts_used, d.validation, d.context, j.state job_state "
                               "FROM drafts d JOIN jobs j ON j.id = d.job_id WHERE j.state IN ('awaiting_approval','needs_review') "
                               "AND d.status = 'pending' ORDER BY d.created_at DESC LIMIT 50"),
            "outbox": rows(db, "SELECT o.id, o.target, o.text, o.state, o.approved_by, o.updated_at, "
                               "(SELECT COUNT(*) FROM delivery_attempts a WHERE a.outbox_id = o.id) attempts FROM outbox o ORDER BY o.updated_at DESC LIMIT 50"),
            "follower_events": rows(db, "SELECT username, source, observed_at FROM follower_events ORDER BY observed_at DESC LIMIT 20"),
            "incoming": rows(db, "SELECT a.username, m.text, m.observed_at, m.id_source FROM messages m JOIN conversations c ON c.id = m.conversation_id "
                                 "JOIN accounts a ON a.id = c.account_id WHERE m.direction = 'in' ORDER BY m.observed_at DESC LIMIT 20"),
            "skipped": rows(db, "SELECT j.subject_key, j.kind, j.state, t.reason, t.at FROM jobs j JOIN job_transitions t ON t.job_id = j.id "
                                "AND t.to_state = j.state WHERE j.state IN ('skipped','needs_review','blocked') ORDER BY t.at DESC LIMIT 50"),
            "not_contacted": not_contacted(),
            "recovery": rows(db, "SELECT at, scope, subject, category, decision, detail FROM recovery_events ORDER BY id DESC LIMIT 50"),
            "errors": rows(db, "SELECT id, subject_key, error_category, last_error, updated_at FROM jobs WHERE last_error IS NOT NULL "
                               "ORDER BY updated_at DESC LIMIT 20"),
            "journal": rows(db, "SELECT target, observation, basis, uncertainty, created_at FROM journal ORDER BY created_at DESC LIMIT 30"),
            "metrics": metrics.snapshot(),
            "version": __version__,
            "summary": {
                "audience": app.settings.policy.audience,
                "sent_today": db.scalar("SELECT COUNT(*) FROM outbox WHERE state = 'sent' AND updated_at >= ?",
                                        (iso(now() - timedelta(days=1)),)),
                "daily_limit": app.settings.limits.daily_send_limit,
                "on_list": db.scalar("SELECT COUNT(*) FROM accounts WHERE follower_source IS NOT NULL"),
                "hellos_waiting_turn": db.scalar("SELECT COUNT(*) FROM jobs WHERE kind = 'intro' AND state = 'queued'"),
                "hellos_sent": db.scalar("SELECT COUNT(*) FROM accounts WHERE intro_status = 'sent'"),
                "list_checked_at": db.get_state("list_checked_at"),
            },
        }

    @api.post("/api/control/{action}", dependencies=[Depends(auth)])
    def control(action: str):
        c = engine.controls
        try:
            {"pause": lambda: c.pause("paused by operator", "operator"), "resume": lambda: c.resume("operator"),
             "stop": lambda: c.stop("stopped by operator", "operator"), "emergency-stop": lambda: c.emergency_stop("operator"),
             "clear-emergency-stop": lambda: c.clear_emergency_stop("operator"),
             "close-circuit": lambda: engine.circuit.record_success()}[action]()
        except KeyError:
            raise HTTPException(404, "unknown action")
        except Exception as e:
            raise HTTPException(409, str(e))
        wake()
        return {"ok": True, "run_state": c.run_state.value}

    @api.post("/api/drafts/{draft_id}/approve", dependencies=[Depends(auth)])
    def approve(draft_id: str, body: ApproveBody):
        try:
            engine.approve_draft(draft_id, body.text)
        except Exception as e:
            raise HTTPException(409, str(e))
        wake()
        return {"ok": True}

    @api.post("/api/drafts/approve-all", dependencies=[Depends(auth)])
    def approve_all(body: ApproveAllBody):
        """Approve, as shown, the hellos the operator was looking at (their ids come from the page). Replies and
        anything flagged for review are left for one-by-one approval."""
        done, refused = 0, []
        for draft_id in body.ids:
            d = db.one("SELECT d.context, j.state, j.kind FROM drafts d JOIN jobs j ON j.id = d.job_id WHERE d.id = ?", (draft_id,))
            if d is None or d["kind"] != "intro" or d["state"] != "awaiting_approval":
                refused.append(draft_id)
                continue
            try:
                engine.approve_draft(draft_id)
                done += 1
            except Exception:
                refused.append(draft_id)
        wake()
        return {"ok": True, "approved": done, "refused": len(refused)}

    @api.post("/api/drafts/{draft_id}/reject", dependencies=[Depends(auth)])
    def reject(draft_id: str):
        try:
            engine.reject_draft(draft_id)
        except Exception as e:
            raise HTTPException(409, str(e))
        return {"ok": True}

    @api.post("/api/outbox/{outbox_id}/resolve", dependencies=[Depends(auth)])
    def resolve(outbox_id: str, body: ResolveBody):
        try:
            engine.resolve_unknown(outbox_id, body.delivered)
        except Exception as e:
            raise HTTPException(409, str(e))
        wake()
        return {"ok": True}

    @api.post("/api/jobs/{job_id}/{action}", dependencies=[Depends(auth)])
    def job_action(job_id: str, action: str):
        from ..core.states import JobState
        target = {"retry": JobState.QUEUED, "skip": JobState.SKIPPED, "cancel": JobState.CANCELLED}.get(action)
        if not target:
            raise HTTPException(404, "unknown action")
        try:
            engine.jobs.transition(job_id, target, f"operator: {action}")
            db.audit("operator", f"job_{action}", {"job": job_id})
        except Exception as e:
            raise HTTPException(409, str(e))
        wake()
        return {"ok": True}

    @api.get("/api/memory/{username}", dependencies=[Depends(auth)])
    def export_memory(username: str):
        return {"username": username, "memory": rows(db, "SELECT kind, content, source, confidence, created_at FROM memory WHERE target = ?", (username,))}

    @api.delete("/api/memory/{username}", dependencies=[Depends(auth)])
    def forget(username: str):
        return {"deleted": engine.forget(username)}

    @api.get("/api/diagnostics", dependencies=[Depends(auth)])
    def diagnostics():
        """Sanitized bundle: no secrets, no message text, no memory contents."""
        cfg = json.loads(app.settings.model_dump_json())
        cfg["llm"]["api_key"] = "[set]" if app.settings.llm.api_key else None
        return {
            "versions": {"python": pyplatform.python_version(), **{p: version(p) for p in ("fastapi", "pydantic", "playwright", "anthropic")}},
            "config": cfg, "health": health(), "status": {k: status()[k] for k in ("run_state", "pause_reason", "circuit", "jobs_by_state")},
            "recent_errors": [{**e, "last_error": redact(e["last_error"] or "")} for e in rows(db,
                              "SELECT id, error_category, last_error, updated_at FROM jobs WHERE last_error IS NOT NULL ORDER BY updated_at DESC LIMIT 50")],
            "transitions": rows(db, "SELECT job_id, from_state, to_state, reason, at FROM job_transitions ORDER BY id DESC LIMIT 200"),
            "recovery": rows(db, "SELECT * FROM recovery_events ORDER BY id DESC LIMIT 100"),
            "metrics": metrics.snapshot(),
            "version": __version__,
            "summary": {
                "audience": app.settings.policy.audience,
                "sent_today": db.scalar("SELECT COUNT(*) FROM outbox WHERE state = 'sent' AND updated_at >= ?",
                                        (iso(now() - timedelta(days=1)),)),
                "daily_limit": app.settings.limits.daily_send_limit,
                "on_list": db.scalar("SELECT COUNT(*) FROM accounts WHERE follower_source IS NOT NULL"),
                "hellos_waiting_turn": db.scalar("SELECT COUNT(*) FROM jobs WHERE kind = 'intro' AND state = 'queued'"),
                "hellos_sent": db.scalar("SELECT COUNT(*) FROM accounts WHERE intro_status = 'sent'"),
                "list_checked_at": db.get_state("list_checked_at"),
            },
        }

    return api
