"""The Command Centre in a real browser: every page in the sidebar opens without a script error, nothing spills
sideways on a phone, and the main actions do what they say."""
from __future__ import annotations

import shutil
import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from assistant import __version__
from assistant.app import Assistant, create_api
from assistant.brain import never_list, prepare_hellos
from assistant.config import AssistantSettings
from assistant.export import apply_export, read_export
from assistant.hub import after_download
from assistant.practice import PracticeLLM, World
from assistant.store import Store

pytestmark = pytest.mark.browser
DIST = Path(__file__).parent.parent / "assistant" / "web" / "dist" / "index.html"
ROUTES = ["", "strategist", "notifications", "accounts", "launch", "groups", "studio", "library", "calendar", "scheduled", "hello",
          "conversations", "contacts", "engagement", "collaborations", "analytics", "missions", "goals", "independence",
          "automations", "automation-history", "reports", "integrations", "api-connections", "ai", "settings", "updates", "phone", "followups", "review/hellos", "review/replies"]


def until(check, timeout_s=8.0):
    end = time.time() + timeout_s
    while time.time() < end:
        if check():
            return True
        time.sleep(0.1)
    return False


@pytest.fixture
def running(tmp_path):
    if not DIST.exists():
        pytest.skip("the Command Centre isn't built (cd web && npm run build)")
    shutil.copy(Path(__file__).parent / "fixtures" / "profile.json", tmp_path / "profile.json")
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "profile.json",
                          never_list_path=tmp_path / "never.txt", downloads_dir=tmp_path / "dl")
    world = World("lukehorbach2")
    a = Assistant(s, store=Store(tmp_path / "assistant.db"), make_web=lambda: None, llm=PracticeLLM(), notify=lambda *x: None,
                  practice=world, env_path=tmp_path / ".env")
    summary = apply_export(a.store, read_export([world.new_download(tmp_path / "dl")]), "lukehorbach2")
    after_download(a.store, "lukehorbach2", summary)
    prepare_hellos(a.store, a.profile(), never_list(s.never_list_path))
    a._write(now=True)
    with socket.socket() as so:
        so.bind(("127.0.0.1", 0))
        port = so.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_api(a, "tok"), host="127.0.0.1", port=port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    until(lambda: server.started)
    yield a, f"http://127.0.0.1:{port}"
    server.should_exit = True
    world.stop()


def test_every_page_opens_cleanly_on_desktop_and_phone(running):
    from playwright.sync_api import sync_playwright
    a, url = running
    a.store.set("whats_new_seen", __version__)          # (the "What's new" box is checked in the next test)
    errors = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        for label, kw in (("desktop", {"viewport": {"width": 1440, "height": 900}}), ("phone", {"viewport": {"width": 390, "height": 844}, "is_mobile": True})):
            page = b.new_page(**kw)
            page.on("pageerror", lambda e, label=label: errors.append(f"{label}: {e}"))
            page.on("console", lambda m, label=label: errors.append(f"{label}: {m.text}") if m.type == "error" else None)
            page.goto(url + "/#token=tok")
            page.wait_for_selector("h1")
            for r in ROUTES:
                page.goto(f"{url}/#/{r}")
                page.wait_for_selector("main h1", timeout=8000)
                page.wait_for_timeout(250)
                over = page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                assert over <= 1, f"{label} /{r} spills sideways by {over}px"
            page.close()
        b.close()
    assert errors == []


def test_main_actions_work(running):
    from playwright.sync_api import sync_playwright
    a, url = running
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        page.goto(url + "/#token=tok")
        page.wait_for_selector("text=Good")
        # After an update, "What's new" shows once; "Got it" puts it away for good.
        page.wait_for_selector("role=dialog >> text=What's new in Convo")
        page.click("role=dialog >> text=Got it")
        assert until(lambda: a.store.get("whats_new_seen") is not None)
        page.reload()
        page.wait_for_selector("text=Good")
        page.wait_for_timeout(600)
        assert not page.is_visible("role=dialog")
        # Dashboard shows real numbers from the download.
        assert "Followers" in page.inner_text("main") and "Instagram · from your download" in page.inner_text("main")
        # Launch network: add an account through the form.
        page.goto(url + "/#/launch")
        page.click("text=Add account")
        page.select_option("#f-platform", "tiktok")
        page.fill("#f-handle", "@luke.kicks")
        page.fill("#f-purpose", "Short football clips")
        page.click("role=dialog >> text=Add account")
        assert until(lambda: a.store.scalar("SELECT COUNT(*) FROM accounts WHERE role = 'launch' AND handle = 'luke.kicks'") == 1)
        page.wait_for_selector("text=@luke.kicks")
        # Conversations: edit and approve a reply.
        page.goto(url + "/#/conversations")
        page.wait_for_selector("#reply")
        page.fill("#reply", "sounds good, see you sunday")
        page.click("main >> text=Approve")
        assert until(lambda: (a.store.one("SELECT text FROM drafts WHERE status = 'approved' AND kind = 'reply'") or {}).get("text")
                     == "sounds good, see you sunday")
        # Contacts: open a person and save a note.
        page.goto(url + "/#/contacts")
        page.click("text=Maya Chen")
        page.fill("#note", "met at the bake sale")
        page.click("text=Save note")
        assert until(lambda: a.store.scalar("SELECT note FROM people WHERE username = 'maya.paints'") == "met at the bake sale")
        page.keyboard.press("Escape")
        # Automation Centre: turn on quiet hours.
        page.goto(url + "/#/automations")
        page.click("role=switch[name='Quiet hours']")
        assert until(lambda: a.s.quiet_from == "23:00" and a.s.quiet_to == "08:00")
        # Ask (Cmd-K) with a free command.
        page.keyboard.press("Control+k")
        page.fill("input[aria-label='Ask Convo']", "status")
        page.keyboard.press("Enter")
        page.wait_for_selector("text=AI spent today")
        page.keyboard.press("Escape")
        # Stop now: the top bar changes at once to "Stopped" with "Start again", which asks before sending.
        assert page.is_visible("header >> [aria-label^='Status:']")
        page.click("header >> text=Stop now")
        assert until(a.sender.halt.is_set)
        page.wait_for_selector("header >> text=Stopped by you", timeout=3000)
        assert "Stopped by you" in page.get_attribute("header >> [aria-label^='Status:']", "aria-label")
        page.click("header >> text=Start again")
        page.wait_for_selector("role=dialog >> text=Start sending again?")
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)
        assert a.sender.halt.is_set() and not a.sender.active          # cancelled: still stopped
        # The hellos queue was never touched by any of this.
        assert a.store.scalar("SELECT COUNT(*) FROM drafts WHERE kind = 'hello' AND status = 'draft'") == 3
        b.close()


def test_the_classic_screens_are_one_click_away(running):
    from playwright.sync_api import sync_playwright
    a, url = running
    a.store.set("whats_new_seen", __version__)
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page()
        page.goto(url + "/#token=tok")
        page.wait_for_selector("text=Good")
        page.click("text=Classic screens")
        page.wait_for_selector("#title")                     # the classic Convo screens, already signed in
        assert "/classic" in page.url
        page.click("text=Command Centre")
        page.wait_for_selector("text=Good")
        b.close()


def test_reviewing_hellos_in_the_new_design(running):
    """Edit and approve one, skip one, undo, then approve all ready with the typed confirmation and undo that too.
    Nothing is sent at any point."""
    from playwright.sync_api import expect, sync_playwright
    a, url = running
    a.store.set("whats_new_seen", __version__)
    count = lambda status: a.store.scalar("SELECT COUNT(*) FROM drafts WHERE kind = 'hello' AND status = ?", (status,))
    assert count("draft") == 3
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(viewport={"width": 1400, "height": 900})
        page.goto(url + "/#token=tok")
        page.wait_for_selector("text=Good")
        page.goto(url + "/#/hello")
        page.click("main >> text=Review hellos")
        page.wait_for_selector("main textarea")
        assert page.locator("main textarea").count() == 3
        # Edit and approve the first.
        first = page.locator("main textarea").first
        first.fill("hey :) loved your last post")
        page.locator("main").get_by_role("button", name="Approve", exact=True).first.click()
        assert until(lambda: count("approved") == 1)
        assert a.store.scalar("SELECT text FROM drafts WHERE kind = 'hello' AND status = 'approved'") == "hey :) loved your last post"
        expect(page.locator("main textarea")).to_have_count(2)
        # Skip the next, then Undo it from the notification.
        page.locator("main").get_by_role("button", name="Skip", exact=True).first.click()
        assert until(lambda: count("skipped") == 1)
        page.locator("[role=status]", has_text="Skipped").get_by_role("button", name="Undo").click()
        assert until(lambda: count("skipped") == 0 and count("draft") == 2)
        # Search narrows the list.
        page.fill("input[aria-label='Search hellos']", "zzz-nobody")
        page.wait_for_selector("text=Nobody by that name here")
        page.fill("input[aria-label='Search hellos']", "")
        page.wait_for_selector("main textarea")
        # Approve all ready, then Undo puts them back.
        page.click("main >> text=Approve all ready")
        page.wait_for_selector("role=dialog >> text=Approve all")
        page.click("role=dialog >> button:has-text('Approve 2')")
        assert until(lambda: count("approved") == 3)
        page.locator("[role=status]", has_text="Approved 2 hellos").get_by_role("button", name="Undo").click()
        assert until(lambda: count("approved") == 1 and count("draft") == 2)
        # The approved tab shows the one approved by hand.
        page.locator("main").get_by_role("button", name="Approved").first.click()
        page.wait_for_selector("main >> text=Undo approval")
        b.close()
    assert count("sent") == 0 and not a.sender.active                      # nothing was sent
