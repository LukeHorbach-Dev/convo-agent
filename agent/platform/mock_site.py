"""A small synthetic social website over MockWorld, so the browser adapter can be tested with a real Chromium.

Faults from MockWorld.faults apply here too, plus web-only faults:
  hang_paths: paths that never finish loading; send_hang_s: the send request delivers then stalls.
"""
from __future__ import annotations

import html
import threading
import time

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .mock import MockWorld

E = html.escape


def page(world: MockWorld, body: str, title: str = "Mock") -> HTMLResponse:
    nav = f'<nav><a data-testid="nav-profile" href="/{E(world.me)}/">Profile</a> <a href="/direct/inbox/">Messages</a></nav>'
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><title>{E(title)}</title></head><body>{nav}<main>{body}</main></body></html>")


def create_site(world: MockWorld) -> FastAPI:
    app = FastAPI()
    app.state.web = {"hang_paths": set(), "send_hang_s": 0.0, "drift": False}

    @app.middleware("http")
    async def faults(request: Request, call_next):
        f = world.faults
        path = request.url.path
        if path in app.state.web["hang_paths"]:
            import asyncio
            await asyncio.sleep(3600)
        if f.outage:
            return HTMLResponse("<h1>Service unavailable</h1>", status_code=503)
        if f.rate_limited:
            return HTMLResponse("<h1>Please wait a few minutes</h1>", status_code=429)
        if f.auth_expired and not path.startswith("/accounts/login"):
            return RedirectResponse("/accounts/login/?next=" + path)
        return await call_next(request)

    @app.get("/accounts/login/")
    def login():
        return HTMLResponse('<form data-testid="login-form"><input name="username"><input name="password" type="password"></form>')

    @app.get("/direct/inbox/")
    def inbox():
        with world.lock:
            links = "".join(f'<li><a data-testid="thread-link" href="/direct/t/{k}/">{E(t["username"])}</a></li>'
                            for k, t in world.threads.items() if t["messages"])
        return page(world, f'<h1 data-testid="inbox-ready">Inbox</h1><ul>{links}</ul>', "Inbox")

    @app.get("/direct/t/{key}/")
    def thread(key: str):
        with world.lock:
            t = world.threads.get(key)
            if not t:
                return HTMLResponse("not found", status_code=404)
            users = [t["username"]] + ([t["username"] + "_alt"] if t["username"] in world.faults.ambiguous else [])
            head = "".join(f'<a data-testid="thread-username" href="/{E(u)}/">{E(u)}</a>' for u in users)
            msgs = "".join(f'<div data-testid="message" data-direction="{m["dir"]}" data-message-id="{m["id"]}">{E(m["text"])}</div>'
                           for m in t["messages"])
        drift = app.state.web["drift"]
        composer = 'data-testid="composer-v2"' if drift else 'data-testid="composer"'
        return page(world, f"""<header>{head}</header><section id="msgs">{msgs}</section>
            <div {composer} role="textbox" contenteditable="true"></div>
            <div role="button" data-testid="send-button" onclick="send()">Send</div>
            <script>
            async function send() {{
              const box = document.querySelector('[role=textbox]'); const text = box.innerText.trim(); if (!text) return;
              const r = await fetch('/api/send', {{method: 'POST', headers: {{'content-type': 'application/json'}},
                                     body: JSON.stringify({{thread: '{key}', text}})}});
              const j = await r.json(); if (!j.ok) return;
              const d = document.createElement('div'); d.dataset.testid = 'message'; d.dataset.direction = 'out';
              d.dataset.messageId = j.id; d.textContent = text; document.getElementById('msgs').appendChild(d); box.innerText = '';
            }}
            </script>""", "Thread")

    @app.post("/api/send")
    async def api_send(request: Request):
        body = await request.json()
        f = world.faults
        mode = f.send_mode
        if f.send_mode_once:
            f.send_mode = "normal"
        if mode == "fail":
            return JSONResponse({"ok": False, "error": "rejected"})
        if mode == "unknown_not_delivered":
            import asyncio
            await asyncio.sleep(app.state.web["send_hang_s"] or 30)
            return JSONResponse({"ok": False})
        mid = world.deliver(body["thread"], body["text"])
        if mode == "unknown_delivered" or app.state.web["send_hang_s"]:
            import asyncio
            await asyncio.sleep(app.state.web["send_hang_s"] or 30)
        return JSONResponse({"ok": True, "id": mid})

    @app.get("/{username}/followers/")
    def followers(username: str, request: Request):
        # Like Instagram: loading /followers/ directly can show just the profile, with no list. The list opens when
        # you arrive from the profile's followers link.
        if not (request.headers.get("referer") or "").endswith(f"/{username}/"):
            return page(world, '<div role="dialog" style="display:none"></div>'
                               f'<h1 data-testid="profile-username">{E(username)}</h1>', "Profile")
        with world.lock:
            items = "".join(f'<li><a data-testid="follower" data-display-name="{E(world.accounts[e["username"]].display_name)}" '
                            f'href="/{E(e["username"])}/">{E(e["username"])}</a></li>' for e in world.follower_events)
        # Like Instagram: the list is a modal role=dialog, and an empty, hidden role=dialog sits earlier in the DOM.
        return page(world, '<div role="dialog" style="display:none"></div>'
                           f'<div role="dialog"><h1>Followers</h1><ul>{items}</ul></div>', "Followers")

    @app.get("/{username}/")
    def profile(username: str):
        with world.lock:
            acct = world.accounts.get(username)
            if username == world.me:
                return page(world, f'<h1 data-testid="profile-username">{E(username)}</h1>'
                                   f'<a href="/{E(username)}/followers/">{len(world.follower_events)} followers</a>')
            if not acct:
                return HTMLResponse("not found", status_code=404)
            f = world.faults
            if username in f.no_message_button:
                btn = '<span data-testid="messaging-restricted">You can\'t message this account</span>'
            elif f.selector_drift_all or username in f.selector_drift_for or app.state.web["drift"]:
                btn = '<div role="button" data-testid="msg-btn-v2">Message</div>'
            else:
                key = world.thread_for(username)
                btn = f'<a role="button" data-testid="message-button" href="/direct/t/{key}/">Message</a>'
        return page(world, f'<header><h1 data-testid="profile-username">{E(username)}</h1><p>{E(acct.bio)}</p>{btn}</header>')

    return app


class SiteServer:
    """Runs the mock site in a background thread (tests and `python -m agent mock-site`)."""

    def __init__(self, world: MockWorld, port: int = 8899):
        self.app = create_site(world)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.url = f"http://127.0.0.1:{port}"

    def start(self) -> "SiteServer":
        self.thread.start()
        deadline = time.monotonic() + 10
        while not self.server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("mock site did not start")
            time.sleep(0.05)
        return self

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(5)
