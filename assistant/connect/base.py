"""Connecting Convo to YouTube, Instagram and TikTok through their official APIs, with your own developer apps.

How it works (the same for each platform):
  1. You make a free developer app on the platform (step-by-step in Convo, Integrations) and paste its Client ID and
     secret into Convo. They're saved on this Mac only, readable by you alone (data/integrations/<platform>.json).
  2. Connect opens the platform's own sign-in page in your browser. You sign in there (Convo never sees your
     password) and allow the access listed. The platform sends a one-time code back to Convo on this Mac.
  3. Convo swaps the code for access keys (OAuth with PKCE, so an intercepted code is useless), and keeps them in the
     same file. It refreshes them by itself, and Disconnect deletes them.
Nothing here posts or replies without you: publishing happens only from a post you scheduled or pressed Publish on.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import httpx


class ConnectError(Exception):
    """Something Luke can read and act on."""


@dataclass
class Platform:
    id: str
    name: str
    auth_url: str
    token_url: str
    scopes: list[str]
    redirect: str = "loopback"          # "loopback": http://127.0.0.1:<port>/oauth/<id>; "relay": the https page that hands back
    extra_auth: dict = field(default_factory=dict)
    client_id_param: str = "client_id"   # TikTok calls it client_key
    scope_sep: str = " "


RELAY = "https://lukehorbach-dev.github.io/convo-agent/oauth.html"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


class Vault:
    """The keys for one platform, in data/integrations/<id>.json, owner-only."""

    def __init__(self, folder: Path, pid: str):
        self.path = Path(folder) / f"{pid}.json"
        self.lock = threading.Lock()

    def read(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            return {}

    def write(self, data: dict) -> None:
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
            os.chmod(tmp, 0o600)
            tmp.replace(self.path)

    def update(self, **kw) -> dict:
        d = {**self.read(), **kw}
        self.write(d)
        return d

    def forget_tokens(self) -> None:
        d = self.read()
        for k in ("tokens", "account", "connected_at", "last_sync", "error"):
            d.pop(k, None)
        self.write(d)


class Connection:
    """One platform's sign-in, keys and requests. Platform connectors (youtube.py, ...) build on this."""

    def __init__(self, platform: Platform, folder: Path, port: int, http: httpx.Client | None = None,
                 log: Callable[[str, str], None] = lambda k, t: None):
        self.p, self.port, self.log = platform, port, log
        self.vault = Vault(folder, platform.id)
        self.http = http or httpx.Client(timeout=httpx.Timeout(30, read=120), follow_redirects=True)
        self.pending: dict[str, tuple[str, float]] = {}       # state -> (PKCE verifier, when)
        self.busy = threading.Lock()

    # ------------------------------------------------------------------ status
    @property
    def redirect_uri(self) -> str:
        return RELAY if self.p.redirect == "relay" else f"http://127.0.0.1:{self.port}/oauth/{self.p.id}"

    def status(self) -> dict:
        d = self.vault.read()
        return {"id": self.p.id, "name": self.p.name, "has_app": bool(d.get("client_id") and d.get("client_secret")),
                "client_id": d.get("client_id"), "connected": bool(d.get("tokens", {}).get("refresh_token") or d.get("tokens", {}).get("access_token")),
                "account": d.get("account"), "connected_at": d.get("connected_at"), "last_sync": d.get("last_sync"),
                "error": d.get("error"), "redirect_uri": self.redirect_uri, "scopes": self.p.scopes}

    def save_app(self, client_id: str, client_secret: str) -> None:
        cid, sec = client_id.strip(), client_secret.strip()
        if len(cid) < 8 or len(sec) < 8 or any(c.isspace() for c in cid + sec):
            raise ConnectError("That Client ID or secret doesn't look complete. Copy them again from the developer site.")
        old = self.vault.read()
        if old.get("client_id") and old.get("client_id") != cid:
            self.vault.forget_tokens()                        # a different app: the old keys don't belong to it
        self.vault.update(client_id=cid, client_secret=sec)

    # ------------------------------------------------------------------ signing in
    def start(self) -> str:
        d = self.vault.read()
        if not (d.get("client_id") and d.get("client_secret")):
            raise ConnectError(f"Add your {self.p.name} app's Client ID and secret first.")
        verifier, challenge = pkce()
        # The return page for Instagram and TikTok reads the platform and port from the state to find Convo on this Mac.
        state = f"{self.p.id}.{self.port}.{secrets.token_urlsafe(24)}"
        now = time.time()
        self.pending = {k: v for k, v in self.pending.items() if now - v[1] < 900}
        self.pending[state] = (verifier, now)
        q = {self.p.client_id_param: d["client_id"], "redirect_uri": self.redirect_uri, "response_type": "code",
             "scope": self.p.scope_sep.join(self.p.scopes),
             "state": state, "code_challenge": challenge, "code_challenge_method": "S256", **self.p.extra_auth}
        return f"{self.p.auth_url}?{urllib.parse.urlencode(q)}"

    def finish(self, code: str, state: str) -> dict:
        got = self.pending.pop(state, None)
        if not got or time.time() - got[1] > 900:
            raise ConnectError("That sign-in link was old or not from this Convo. Press Connect again.")
        d = self.vault.read()
        tokens = self._token({"grant_type": "authorization_code", "code": code, "redirect_uri": self.redirect_uri,
                              "code_verifier": got[0]}, d)
        tokens = self.after_login(tokens, d)
        self.vault.update(tokens=tokens, connected_at=_now(), error=None)
        self.log("good", f"{self.p.name} is connected.")
        return tokens

    def _token(self, form: dict, d: dict) -> dict:
        body = {**form, self.p.client_id_param: d["client_id"], "client_secret": d["client_secret"]}
        try:
            r = self.http.post(self.p.token_url, data=body, headers={"Accept": "application/json"})
        except httpx.HTTPError as e:
            raise ConnectError(f"Couldn't reach {self.p.name}. Check the internet connection.") from e
        try:
            j = r.json()
        except ValueError:
            j = {}
        if r.status_code >= 400 or "access_token" not in j:
            why = j.get("error_description") or j.get("error") or j.get("message") or f"error {r.status_code}"
            raise ConnectError(f"{self.p.name} didn't accept that ({why}).")
        old = d.get("tokens") or {}
        return {"access_token": j["access_token"], "refresh_token": j.get("refresh_token") or old.get("refresh_token"),
                "expires_at": time.time() + float(j.get("expires_in") or 3600), "scope": j.get("scope") or old.get("scope"),
                **({"open_id": j["open_id"]} if "open_id" in j else {}), **({"user_id": j["user_id"]} if "user_id" in j else {})}

    def after_login(self, tokens: dict, d: dict) -> dict:
        """Platforms that hand out a short-lived key first swap it here (Instagram)."""
        return tokens

    refresh_before_s = 90

    def refresh(self, t: dict, d: dict) -> dict:
        if not t.get("refresh_token"):
            return t
        return self._token({"grant_type": "refresh_token", "refresh_token": t["refresh_token"]}, d)

    def access_token(self) -> str:
        d = self.vault.read()
        t = d.get("tokens") or {}
        if not t.get("access_token"):
            raise ConnectError(f"{self.p.name} isn't connected.")
        if t.get("expires_at", 0) > time.time() + self.refresh_before_s:
            return t["access_token"]
        t = self.refresh(t, d)
        self.vault.update(tokens=t)
        return t["access_token"]

    # ------------------------------------------------------------------ requests
    def request(self, method: str, url: str, **kw) -> httpx.Response:
        try:
            r = self.http.request(method, url, headers={"Authorization": f"Bearer {self.access_token()}", **kw.pop("headers", {})}, **kw)
        except httpx.HTTPError as e:
            raise ConnectError(f"Couldn't reach {self.p.name}. Check the internet connection.") from e
        if r.status_code == 401:
            self.vault.update(error=f"{self.p.name} signed Convo out. Press Connect again.")
            raise ConnectError(f"{self.p.name} signed Convo out. Press Connect again.")
        if r.status_code >= 400:
            raise ConnectError(f"{self.p.name} said no ({_why(r)}).")
        return r

    def get(self, url: str, **params) -> dict:
        return self.request("GET", url, params=params).json()

    def disconnect(self) -> None:
        self.vault.forget_tokens()
        self.log("info", f"{self.p.name} disconnected. Its keys were deleted from this Mac.")


def _why(r: httpx.Response) -> str:
    try:
        j = r.json()
    except ValueError:
        return f"error {r.status_code}"
    e = j.get("error")
    if isinstance(e, dict):
        reason = (e.get("errors") or [{}])[0].get("reason") if e.get("errors") else None
        if reason == "quotaExceeded":
            return "today's YouTube allowance is used up; it resets at midnight Pacific time"
        return e.get("message") or reason or f"error {r.status_code}"
    return j.get("error_description") or (e if isinstance(e, str) else None) or j.get("message") or f"error {r.status_code}"


def _now() -> str:
    from ..store import now_iso
    return now_iso()
