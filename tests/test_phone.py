"""Phone access: off by default; when on, Convo also answers on the Tailscale address (and only there), still
asking for its password; turning it off closes that address again."""
from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request

import pytest

from assistant.app import Assistant, create_api
from assistant.config import AssistantSettings
from assistant.phone import PhoneAccess, tailscale_ip
from assistant.practice import PracticeLLM
from assistant.store import Store


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def get(url: str, token: str | None = None) -> int:
    req = urllib.request.Request(url, headers={"X-Agent-Token": token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


@pytest.fixture
def convo(tmp_path):
    port = free_port()
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "p.json", never_list_path=tmp_path / "n.txt",
                          dashboard_port=port)
    a = Assistant(s, store=Store(tmp_path / "a.db"), make_web=lambda: None, llm=PracticeLLM(), notify=lambda *x: None,
                  env_path=tmp_path / ".env")
    a.phone = PhoneAccess(port, log=a.store.log, find_ip=lambda: "127.0.0.2", find_name=lambda: None)
    create_api(a, "tok")
    yield a, port
    a.phone.stop()


def test_phone_access_opens_only_the_tailscale_address_and_still_needs_the_password(convo):
    a, port = convo
    phone_url = f"http://127.0.0.2:{port}"
    assert a.phone.hosts == set()
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(phone_url + "/api/health", timeout=1)          # nothing listening there yet
    assert a.phone.start()
    assert get(phone_url + "/api/health") == 200
    assert get(phone_url + "/api/state") == 401                              # the password is still needed
    assert get(phone_url + "/api/state", "tok") == 200
    st = a.phone.status("tok")
    assert st["listening"] and st["url"] == f"http://127.0.0.2:{port}/#token=tok"
    a.phone.stop()
    time.sleep(0.3)
    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(phone_url + "/api/health", timeout=1)
    assert a.phone.hosts == set()


def test_without_tailscale_it_says_so(tmp_path):
    p = PhoneAccess(free_port(), find_ip=lambda: None, find_name=lambda: None)
    p.app = object()
    assert p.start() is False and "Tailscale" in p.error and p.status("t")["url"] is None


def test_the_setting_turns_it_on_and_off_and_is_saved(convo, tmp_path):
    from fastapi.testclient import TestClient
    a, port = convo
    c = TestClient(a.phone.app, base_url="http://127.0.0.1")
    h = {"X-Agent-Token": "tok"}
    assert c.get("/api/phone", headers=h).json()["on"] is False
    assert c.post("/api/settings", json={"phone_access": True}, headers=h).status_code == 200
    for _ in range(40):
        if a.phone.listening:
            break
        time.sleep(0.1)
    assert a.phone.listening and "AGENT_PHONE_ACCESS=true" in (tmp_path / ".env").read_text()
    assert c.get("/api/phone", headers=h).json()["url"].startswith("http://127.0.0.2:")
    assert c.post("/api/settings", json={"phone_access": False}, headers=h).status_code == 200
    assert not a.phone.listening and "AGENT_PHONE_ACCESS=false" in (tmp_path / ".env").read_text()


def test_a_tailscale_address_is_recognised(monkeypatch):
    from assistant import phone
    monkeypatch.setattr(phone.shutil, "which", lambda n: "/sbin/ifconfig" if n == "ifconfig" else None)
    monkeypatch.setattr(phone, "_run", lambda cmd: "lo0: inet 127.0.0.1 netmask\nen0: inet 192.168.1.20\nutun4: inet 100.101.7.12 --> 100.101.7.12\n")
    assert tailscale_ip() == "100.101.7.12"
    monkeypatch.setattr(phone, "_run", lambda cmd: "en0: inet 192.168.1.20\n")
    assert tailscale_ip() is None
