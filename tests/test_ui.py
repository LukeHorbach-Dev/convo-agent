"""The dashboard in a real browser: every page loads without a script error, and the main buttons work."""
from __future__ import annotations

import shutil
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from assistant.app import Assistant, create_api
from assistant.brain import never_list, prepare_hellos
from assistant.config import AssistantSettings
from assistant.export import apply_export, read_export
from assistant.practice import PracticeLLM, World
from assistant.store import Store

pytestmark = pytest.mark.browser
_servers: list = []


def until(check, timeout_s=8.0):
    """Wait for the app to catch up (the page talks to it in the background)."""
    end = time.time() + timeout_s
    while time.time() < end:
        if check():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def running(tmp_path):
    shutil.copy(Path(__file__).parent / "fixtures" / "profile.json", tmp_path / "profile.json")
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "profile.json",
                          never_list_path=tmp_path / "never.txt", downloads_dir=tmp_path / "dl")
    world = World("lukehorbach2")
    a = Assistant(s, store=Store(tmp_path / "a.db"), make_web=lambda: None, llm=PracticeLLM(), notify=lambda *x: None,
                  practice=world, env_path=tmp_path / ".env")
    apply_export(a.store, read_export([world.new_download(tmp_path / "dl")]), "lukehorbach2")
    prepare_hellos(a.store, a.profile(), never_list(s.never_list_path))
    a._write(now=True)
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_api(a, "tok"), host="127.0.0.1", port=port, log_level="warning"))
    _servers.append(server)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        if server.started:
            break
        time.sleep(0.1)
    yield a, f"http://127.0.0.1:{port}"
    for s in _servers:
        s.should_exit = True
    world.stop()


def test_every_page_loads_and_the_main_buttons_work(running):
    from playwright.sync_api import sync_playwright
    a, url = running
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1280, "height": 860})
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.goto(url + "/classic#token=tok")
        page.wait_for_selector("#hero h2")
        assert "ready for you" in page.inner_text("#hero h2")
        assert page.is_visible("#practiceband")                      # practice mode shows its band
        for name in ("replies", "hellos", "people", "ask", "activity", "about", "settings", "help", "today"):
            page.click(f'#nav button[data-page="{name}"]')
            page.wait_for_timeout(250)
            assert page.inner_text("#title")
        # Approve all hellos through the confirm box
        page.click('#nav button[data-page="hellos"]')
        page.wait_for_selector(".msg")
        page.click("text=Approve all ready")
        page.click("#okbtn")
        assert until(lambda: a.store.scalar("SELECT COUNT(*) FROM drafts WHERE kind = 'hello' AND status = 'approved'") == 3)
        # Edit and approve one reply
        page.click('#nav button[data-page="replies"]')
        page.wait_for_selector(".msg textarea")
        body = page.inner_text("#list")
        assert "Voice message" in body and "Listen to it in Instagram" in body       # left for Luke, no AI text
        assert "Reel by @bookclub" in body and "can't see" in body                    # written, but needs his look
        first = page.locator(".msg textarea").first
        first.fill("sounds good, see you then")
        first.dispatch_event("change")
        page.locator(".msg .btn.go").first.click()
        assert until(lambda: (a.store.one("SELECT text FROM drafts WHERE status = 'approved' AND kind = 'reply'") or {}).get("text")
                     == "sounds good, see you then")
        # A person's panel
        page.click('#nav button[data-page="people"]')
        page.wait_for_selector(".prow")
        page.click("text=Maya Chen")
        page.wait_for_selector("#pname")
        assert "saw your post about baking" in page.inner_text(".drawer")
        page.keyboard.press("Escape")
        # Ask (free command)
        page.keyboard.press("Control+k")
        page.fill("#pal", "status")
        page.press("#pal", "Enter")
        page.wait_for_selector("#palout p")
        assert "approved" in page.inner_text("#palout")
        page.keyboard.press("Escape")
        # Stop now
        page.click("#stopbtn")
        assert until(a.sender.halt.is_set)
        # Settings: quiet hours save
        page.click('#nav button[data-page="settings"]')
        page.fill("#qf", "23:00")
        page.fill("#qt", "08:00")
        page.click("#savesending")
        assert until(lambda: a.s.quiet_from == "23:00" and a.s.quiet_to == "08:00")
        # Phone width: the bottom bar
        page.set_viewport_size({"width": 390, "height": 800})
        page.click('#nav button[data-page="today"]')
        assert page.is_visible("#nav")
        b.close()
    assert errors == []


def test_window_waits_while_convo_is_not_running_and_comes_back_by_itself(running):
    """Seen 30 Oct: after Quit, the old window showed "Convo has quit" and opening Convo again seemed to do nothing."""
    from playwright.sync_api import sync_playwright
    a, url = running
    port = int(url.rsplit(":", 1)[1])
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.goto(url + "/classic#token=tok")
        page.wait_for_selector("#hero h2")
        server = next(s for s in _servers if s.config.port == port)
        server.should_exit = True                                   # Convo quits
        page.wait_for_selector("#offline", timeout=10000)
        assert "isn't running" in page.inner_text("#offline")
        again = uvicorn.Server(uvicorn.Config(create_api(a, "tok"), host="127.0.0.1", port=port, log_level="warning"))
        _servers.append(again)
        threading.Thread(target=again.run, daemon=True).start()     # Convo is opened again
        page.wait_for_selector("#offline", state="detached", timeout=15000)   # reloaded on its own
        page.wait_for_selector("#hero h2")
        b.close()
