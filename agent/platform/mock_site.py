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
    app.state.web = {"hang_paths": set(), "send_hang_s": 0.0, "drift": False, "no_expand": False, "swap_lists": False}

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
            # Like Instagram: rows are role=button without links, each with the latest preview and a time.
            rows = "".join(f'<div role="button" data-testid="thread-row" onclick="location.href=\'/direct/t/{k}/\'">'
                           f'{E(t["username"])} {E(t["messages"][-1]["text"][:30])} #{len(t["messages"])} · <abbr>{len(t["messages"])}m</abbr></div>'
                           for k, t in world.threads.items() if t["messages"])
        return page(world, f'<h1 data-testid="inbox-ready">Inbox</h1><div aria-label="Thread list">{rows}</div>', "Inbox")

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

    @app.post("/api/unfollow")
    async def api_unfollow(request: Request):
        body = await request.json()
        with world.lock:
            world.unfollowed.add(body["username"])
        return JSONResponse({"ok": True})

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

    @app.get("/m/{username}/")
    def dm_link(username: str):
        # Like ig.me/m/<user>: opens the conversation with that account.
        with world.lock:
            if username not in world.accounts:
                return HTMLResponse("<p>Sorry, this page isn't available.</p>", status_code=404)
            return RedirectResponse(f"/direct/t/{world.thread_for(username)}/")

    @app.get("/{username}/following/")
    @app.get("/{username}/followers/")
    def followers(username: str, request: Request):
        # Following: the accounts in follower_events. Followers: those plus "fans" (people you don't follow back),
        # so reading the wrong list shows up in tests.
        which = "following" if request.url.path.rstrip("/").endswith("following") else "followers"
        # Like Instagram: loading /followers/ directly can show just the profile, with no list. The list opens when
        # you arrive from the profile's followers link.
        if not (request.headers.get("referer") or "").endswith(f"/{username}/"):
            return page(world, '<div role="dialog" style="display:none"></div>'
                               f'<h1 data-testid="profile-username">{E(username)}</h1>', "Profile")
        with world.lock:
            names = [e["username"] for e in reversed(world.follower_events) if e["username"] not in world.unfollowed]  # newest first
            if which == "followers":
                names = list(reversed(world.fans)) + names
            items = "".join(f'<li><a data-testid="follower" data-display-name="{E(world.accounts[u].display_name)}" '
                            f'href="/{E(u)}/">{E(u)}</a> <div role="button">{"Following" if which == "following" else "Remove"}</div></li>'
                            for u in names)
        # Like Instagram: the list is a modal role=dialog, and an empty, hidden role=dialog sits earlier in the DOM.
        return page(world, '<div role="dialog" style="display:none"></div>'
                           f'<div role="dialog"><h1>{which.title()}</h1><ul>{items}</ul></div>', which.title())

    @app.get("/{username}/")
    def profile(username: str):
        with world.lock:
            acct = world.accounts.get(username)
            if username == world.me:
                return page(world, f'<header><h1 data-testid="profile-username">{E(username)}</h1>'
                                   '<a href="/accounts/edit/">Edit profile</a></header>'
                                   # Live profile (29 Oct): the counts are clickable labels (span role=link), not links.
                                   f'<span role="link" onclick="location.href=\'/{E(username)}/followers/\'">{len(world.follower_events) + len(world.fans)} followers</span> '
                                   f'<span role="link" onclick="location.href=\'/{E(username)}/{"followers" if app.state.web["swap_lists"] else "following"}/\'">{len(world.follower_events)} following</span>')
            if not acct:
                return page(world, "<p>Sorry, this page isn't available.</p>", "Page not found")
            f = world.faults
            if username in f.no_message_button:
                btn = '<span data-testid="messaging-restricted">You can\'t message this account</span>'
            elif f.selector_drift_all or username in f.selector_drift_for or app.state.web["drift"]:
                btn = '<div role="button" data-testid="msg-btn-v2">Message</div>'
            else:
                key = world.thread_for(username)
                # Like Instagram: Message opens a small chat pop-up over the profile; its expand icon opens the
                # full conversation.
                expand = ("" if app.state.web["no_expand"] else
                          f'<div role="button" onclick="location.href=\'/direct/t/{key}/\'">'
                          f'<svg aria-label="Expand" width="16" height="16"><rect width="16" height="16"/></svg></div>')
                btn = (f'<div data-testid="message-button" onclick="document.getElementById(\'chat\').hidden=false">Message</div>'
                       f'<aside id="chat" hidden><b>{E(username)}</b>{expand}'
                       f'<div data-testid="popup-composer" contenteditable="true">Message...</div></aside>')
            script = ""
            if username in world.fans and username not in [e["username"] for e in world.follower_events]:
                btn = ""                                    # like live: no Message button for someone you don't follow
            if username in world.unfollowed or username in world.fans:
                follow = '<div>Follow</div>'
            else:
                # Like Instagram: "Following" opens a menu whose "Unfollow" item unfollows; the button then says Follow.
                follow = ('<div id="fb" onclick="document.getElementById(\'menu\').hidden=false">Following</div>'
                          '<div role="dialog" id="menu" hidden><div role="button" onclick="unf()">Unfollow</div>'
                          '<div role="button" onclick="this.parentElement.hidden=true">Cancel</div></div>')
                script = (f'<script>async function unf() {{ await fetch("/api/unfollow", {{method: "POST", headers: {{"content-type": "application/json"}}, '
                          f'body: JSON.stringify({{username: "{E(username)}"}})}}); document.getElementById("menu").hidden = true; '
                          f'document.getElementById("fb").textContent = "Follow"; }}</script>')
        # Like Instagram: the header (and its Message button) renders a moment after the page loads.
        # Like Instagram, the counts are buttons too ("following" in a count must not be mistaken for the
        # Following button).
        # Like the live site (seen 29 Oct): no <header> element, buttons without roles, and a "suggested for you"
        # strip whose own Follow buttons must not be mistaken for this profile's.
        header = (f'<section><h1 data-testid="profile-username">{E(username)}</h1><p>{E(acct.bio)}</p>'
                  '<div role="button">12 followers</div><div role="button">34 following</div>'
                  f'{follow}{btn}</section>'
                  '<section><h2>Suggested for you</h2><div>anna.k <div role="button">Follow</div></div>'
                  '<div>ben.m <div role="button">Follow</div></div></section>')
        return page(world, f'{script}<div id="app"></div><template id="h">{header}</template><script>setTimeout(() => '
                           f'document.getElementById("app").replaceWith(document.getElementById("h").content), 400)</script>')

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
