"""Phone access: Convo on your phone, through Tailscale.

Tailscale is a free app that joins your Mac and your phone into a private network of your own (encrypted, nobody
else is on it). When phone access is on, Convo also listens on the Mac's Tailscale address (100.x.y.z), so the
phone can open it from home or away. Nothing is opened to the internet or your Wi-Fi: only devices signed in to your
Tailscale can reach that address, and Convo still asks for its password on every request.

Off by default. Settings, Phone access turns it on (AGENT_PHONE_ACCESS=true in .env).
"""
from __future__ import annotations

import ipaddress
import json
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

TAILNET = ipaddress.ip_network("100.64.0.0/10")         # the addresses Tailscale hands out
CLI = ("/Applications/Tailscale.app/Contents/MacOS/Tailscale", "/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale")


def _run(cmd: list[str]) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return ""


def tailscale_ip() -> str | None:
    """This Mac's Tailscale address, read from its network interfaces (no Tailscale command needed)."""
    out = _run(["ifconfig"]) if shutil.which("ifconfig") else _run(["ip", "-4", "addr"])
    for m in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+)", out):
        try:
            if ipaddress.ip_address(m.group(1)) in TAILNET:
                return m.group(1)
        except ValueError:
            continue
    return None


def tailscale_name() -> str | None:
    """This Mac's Tailscale name (like luke-macbook.tail1234.ts.net), if the Tailscale command is there."""
    for cli in (shutil.which("tailscale"), *CLI):
        if cli and Path(cli).exists():
            try:
                name = json.loads(_run([cli, "status", "--json"]) or "{}").get("Self", {}).get("DNSName", "")
            except json.JSONDecodeError:
                name = ""
            if name:
                return name.rstrip(".")
    return None


class PhoneAccess:
    """A second listener for the same Convo, on the Tailscale address only."""

    def __init__(self, port: int, log=lambda kind, text: None, find_ip=tailscale_ip, find_name=tailscale_name):
        self.port, self.log, self.find_ip, self.find_name = port, log, find_ip, find_name
        self.app = None
        self.server = None
        self.ip: str | None = None
        self.name: str | None = None
        self.error: str | None = None
        self.lock = threading.Lock()

    @property
    def hosts(self) -> set[str]:
        """Host names the guard lets in while phone access is on."""
        return {h for h in (self.ip, self.name) if h} if self.listening else set()

    @property
    def listening(self) -> bool:
        return bool(self.server is not None and self.server.started and not self.server.should_exit)

    def start(self, ip: str | None = None) -> bool:
        import uvicorn
        with self.lock:
            if self.listening:
                return True
            self.error = None
            self.ip = ip or self.find_ip()
            if not self.ip:
                self.error = "Tailscale isn't running on this Mac (or isn't signed in), so there's no address for your phone yet."
                return False
            self.name = self.find_name()
            if self.app is None:
                self.error = "Convo isn't ready yet."
                return False
            self.server = uvicorn.Server(uvicorn.Config(self.app, host=self.ip, port=self.port, log_level="warning"))
            threading.Thread(target=self._run, args=(self.server,), name="phone", daemon=True).start()
            for _ in range(50):
                if self.server.started or self.error:
                    break
                time.sleep(0.1)
            if self.listening:
                self.log("good", f"Phone access is on: http://{self.name or self.ip}:{self.port}")
            return self.listening

    def _run(self, server) -> None:
        try:
            server.run()
        except BaseException as e:             # uvicorn exits through SystemExit when the address can't be used
            self.error = f"Couldn't listen on {self.ip}:{self.port} ({e or 'address in use'})."
        if not server.started and not self.error:
            self.error = f"Couldn't listen on {self.ip}:{self.port}."

    def stop(self) -> None:
        with self.lock:
            if self.server is not None:
                self.server.should_exit = True
                for _ in range(50):
                    if not self.server.started:
                        break
                    time.sleep(0.1)
                if self.listening is False and self.ip:
                    self.log("info", "Phone access is off.")
            self.server = None

    def status(self, token: str) -> dict:
        ip = self.ip if self.listening else self.find_ip()
        host = (self.name or ip) if self.listening else None
        return {"listening": self.listening, "tailscale_ip": ip, "tailscale_name": self.name, "error": self.error,
                "url": f"http://{host}:{self.port}/#token={token}" if host else None,
                "mac": sys.platform == "darwin"}
