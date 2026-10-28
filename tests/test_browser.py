"""Browser tests: a real Chromium (Playwright) driving the mock site, with injected browser and site faults."""
from __future__ import annotations

import socket

import pytest

from agent.app import build_app
from agent.core.config import BrowserConfig
from agent.core.errors import (AmbiguousState, AuthenticationRequired, InterventionRequired, PermissionRestricted, PlatformUnavailable,
                               RateLimited, SelectorDrift)
from agent.core.recovery import RunState
from agent.platform.browser import BrowserSupervisor
from agent.platform.mock import MockWorld
from agent.platform.mock_site import SiteServer
from agent.platform.sites import mock_site_profile
from agent.platform.web import WebPlatform

from .conftest import job_states, make_settings, settle

pytestmark = pytest.mark.browser


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def site(tmp_path):
    world = MockWorld("lukehorbach2")
    server = SiteServer(world, free_port()).start()
    profile = mock_site_profile(server.url)
    cfg = BrowserConfig(headless=True, user_data_dir=tmp_path / "profile", navigation_timeout_ms=4000,
                        action_timeout_ms=2000, health_check_timeout_ms=2000, max_recoveries_per_run=2)
    sup = BrowserSupervisor(cfg, profile)
    web = WebPlatform(sup, profile, world.me)
    web.batch_wait_s = 0.5
    yield world, server, web
    web.close()
    server.stop()


def test_web_adapter_reads_and_sends(site):
    world, _, web = site
    world.new_follower("mia.skis", "Mia")
    assert web.whoami() == "lukehorbach2"
    assert [f.username for f in web.poll_followers()] == ["mia.skis"]
    snap = web.open_dm("mia.skis")
    assert snap.username == "mia.skis" and snap.messages == []
    assert web.send(snap.thread_key, "Hey im Luke :)", "op1").status == "sent"
    assert world.sent_log == [("mia.skis", "Hey im Luke :)")]
    assert web.find_sent(snap.thread_key, "Hey im Luke :)", "") == "found"


def test_end_to_end_engine_through_browser(site, db):
    world, _, web = site
    s = make_settings()
    s.policy.intro_existing_followers = False
    world.new_follower("old.follower")
    app = build_app(s, db=db, platform=web, world=world)
    app.engine.run_once()                                       # first observation = baseline, no intros
    assert world.sent_log == []
    assert app.db.get_state("followers_baselined") is True
    world.new_follower("mia.skis", "Mia")
    app.engine.run_once()
    assert [u for u, _ in world.sent_log] == ["mia.skis"]
    world.incoming("mia.skis", "hey just got back from skiing")
    app.engine.run_once()
    assert len(world.sent_log) == 2 and "skiing" in world.sent_log[1][1]
    assert job_states(app.db, "reply")["mia.skis"] == "completed"


def test_empty_followers_read_is_never_the_baseline(site, db):
    """An empty list (still loading, or not found) must not become the baseline, or everyone would look new later."""
    world, _, web = site
    s = make_settings(); s.policy.intro_existing_followers = False
    app = build_app(s, db=db, platform=web, world=world)
    app.engine.run_once()                                       # list is empty: no baseline
    assert not app.db.get_state("followers_baselined", False)
    world.new_follower("old.one"); world.new_follower("old.two")
    app.engine.run_once()                                       # first real read is the baseline
    assert app.db.get_state("followers_baselined") is True
    assert world.sent_log == []


def test_older_follower_below_known_ones_is_not_introduced(site, db):
    """The list is newest first; an unknown name below known ones is an old follower that scrolled into view."""
    world, _, web = site
    s = make_settings(); s.policy.intro_existing_followers = False
    world.new_follower("known.a"); world.new_follower("known.b")
    app = build_app(s, db=db, platform=web, world=world)
    app.engine.run_once()                                       # baseline
    world.new_follower("older.one")
    world.follower_events.insert(0, world.follower_events.pop())   # oldest position: bottom of the list
    world.new_follower("fresh.one")                             # newest: top of the list
    app.engine.run_once()
    assert [u for u, _ in world.sent_log] == ["fresh.one"]


def test_inspect_reports_page_structure_without_clicking(site):
    from agent.platform.inspect_page import inspect
    world, _, web = site
    world.new_follower("mia"); world.incoming("mia", "secret words")
    report = inspect(web, settle_ms=0)
    assert "== profile:" in report and "== inbox:" in report and "== thread:" in report and "== their-profile:" in report
    assert "1 incoming, 0 outgoing" in report and "secret" not in report      # counts only, never message text
    from agent.platform.inspect_page import inspect_dm
    dm = inspect_dm(web, "mia", settle_ms=0)
    assert "== after-message-click:" in dm and world.sent_log == []
    assert "visible /" in report and world.sent_log == []


def test_inbox_rows_are_opened_only_when_they_change(site):
    """Instagram inbox rows have no links: each is clicked. Unchanged rows aren't reopened on the next poll."""
    world, _, web = site
    world.new_follower("mia"); world.new_follower("noah")
    world.incoming("mia", "hey"); world.incoming("noah", "yo")
    assert sorted(t.username for t in web.list_threads()) == ["mia", "noah"]
    assert web.list_threads() == []
    world.incoming("mia", "you there?")
    [snap] = web.list_threads()
    assert snap.username == "mia" and snap.messages[-1].text == "you there?"
    web.full_reread_s = 0                                       # periodic full re-read
    assert len(web.list_threads()) == 2


def test_following_audience_says_hello_to_everyone_you_follow(site, db):
    """audience=following: the list opened is "following", there's no baseline, and everyone on it gets a hello."""
    world, _, _ = site
    s = make_settings(); s.policy.audience = "following"
    from agent.app import build_platform
    from agent.core.config import PlatformKind
    s.platform = PlatformKind.MOCK_WEB
    s.mock_site_url = site[1].url
    s.browser = site[2].sup.cfg
    web, _ = build_platform(s, world)
    web.batch_wait_s = 0.5
    world.new_follower("friend.one"); world.new_follower("friend.two")
    app = build_app(s, db=db, platform=web, world=world)
    try:
        app.engine.run_once(); app.engine.run_once()
        assert web.audience == "following"
        assert sorted(u for u, _ in world.sent_log) == ["friend.one", "friend.two"]
        assert not app.db.get_state("followers_baselined", False)
    finally:
        web.close()


def test_chat_popup_without_expand_falls_back_to_the_dm_address(site):
    """Seen live: Message opens a chat pop-up. If it can't be expanded, the conversation is opened by address."""
    world, server, web = site
    world.new_follower("mia")
    server.app.state.web["no_expand"] = True
    snap = web.open_dm("mia")
    assert snap.username == "mia" and "/direct/t/" in web.page.url


def test_unavailable_profile_is_skipped_not_treated_as_a_broken_page(site):
    """A deleted or renamed account shows "Sorry, this page isn't available": skip that person, don't count it
    toward the page-changed alarm."""
    _, _, web = site
    with pytest.raises(PermissionRestricted):
        web.open_dm("someone.who.left")


# 1. a page that never finishes loading
def test_navigation_timeout_is_classified(site):
    world, server, web = site
    server.app.state.web["hang_paths"].add("/direct/inbox/")
    with pytest.raises(PlatformUnavailable, match="did not load"):
        web.whoami()


# 2. browser crash during navigation, recovered within budget
def test_browser_crash_recovers(site):
    world, _, web = site
    assert web.whoami() == "lukehorbach2"
    web.sup.context.close()                                     # the browser dies
    assert web.whoami() == "lukehorbach2"                       # supervisor notices and restarts
    assert web.sup.restarts == 2 and web.sup.recoveries == 1


def test_browser_recovery_budget_exhausted_pauses(site):
    world, _, web = site
    web.whoami()
    for _ in range(web.sup.cfg.max_recoveries_per_run):
        web.sup.context.close()
        web.whoami()
    web.sup.context.close()
    with pytest.raises(InterventionRequired, match="budget"):
        web.whoami()


def test_engine_pauses_when_browser_budget_exhausted(site, db):
    world, _, web = site
    s = make_settings(); s.policy.intro_existing_followers = True
    app = build_app(s, db=db, platform=web, world=world)
    world.new_follower("mia")
    web.sup.recoveries = web.sup.cfg.max_recoveries_per_run
    web.whoami()
    web.sup.context.close()
    app.engine.run_once()
    assert app.engine.controls.run_state is RunState.PAUSED
    assert world.sent_log == []


# 4. a selector that disappears after a UI change
def test_selector_drift_detected(site):
    world, server, web = site
    world.new_follower("mia")
    server.app.state.web["drift"] = True
    with pytest.raises(SelectorDrift):
        web.open_dm("mia")


def test_missing_page_element_saves_a_screenshot(site, tmp_path):
    _, server, web = site
    with pytest.raises(SelectorDrift):
        web.sup.goto(server.url + "/", expect=".*", ready='[data-testid="does-not-exist"]')
    shot = tmp_path / "diagnostics" / "last-selector-drift-page.png"
    assert shot.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_identity_from_own_profile_page(site):
    """Instagram-style identity check: only your own profile shows Edit profile."""
    import dataclasses
    world, server, web = site
    world.new_follower("mia")
    marker = 'header a[href="/accounts/edit/"]'
    prof = dataclasses.replace(web.site)
    prof.own_profile_marker = lambda: marker
    assert WebPlatform(web.sup, prof, "lukehorbach2").whoami() == "lukehorbach2"
    with pytest.raises(AuthenticationRequired):
        WebPlatform(web.sup, prof, "mia").whoami()


def test_selector_drift_everywhere_opens_circuit(site, db):
    world, server, web = site
    s = make_settings(); s.policy.intro_existing_followers = True
    app = build_app(s, db=db, platform=web, world=world)
    for i in range(s.circuit.failure_threshold + 1):
        world.new_follower(f"user{i}")
    server.app.state.web["drift"] = True
    settle(app.engine, 3)
    assert app.engine.circuit.state == "open"
    assert world.sent_log == []


# 5. session expires
def test_session_expiry_detected_and_engine_pauses(site, db):
    world, _, web = site
    s = make_settings(); s.policy.intro_existing_followers = True
    app = build_app(s, db=db, platform=web, world=world)
    world.new_follower("mia")
    world.faults.auth_expired = True
    with pytest.raises(AuthenticationRequired):
        web.whoami()
    app.engine.run_once()
    assert app.engine.controls.run_state is RunState.PAUSED and world.sent_log == []


# 6. message control unavailable
def test_missing_message_button(site):
    world, _, web = site
    world.new_follower("rae"); world.faults.no_message_button.add("rae")
    with pytest.raises(PermissionRestricted):
        web.open_dm("rae")


# 7. conversation that cannot be uniquely identified
def test_ambiguous_thread(site):
    world, _, web = site
    world.new_follower("twin")
    key = web.open_dm("twin").thread_key
    world.faults.ambiguous.add("twin")
    with pytest.raises(AmbiguousState):
        web.read_thread(key)


def test_site_outage_and_rate_limit(site):
    world, _, web = site
    world.faults.outage = True
    with pytest.raises(PlatformUnavailable):
        web.whoami()
    world.faults.outage = False
    world.faults.rate_limited = True
    with pytest.raises(RateLimited):
        web.whoami()


# 12. send whose confirmation never arrives: reconciled by re-reading the thread, never resent
def test_unconfirmed_send_is_reconciled_not_resent(site, db):
    world, server, web = site
    s = make_settings(); s.policy.intro_existing_followers = True
    app = build_app(s, db=db, platform=web, world=world)
    world.new_follower("mia")
    server.app.state.web["send_hang_s"] = 5                      # server delivers, then stalls past the action timeout
    app.engine.run_once()
    server.app.state.web["send_hang_s"] = 0
    settle(app.engine, 2)
    assert len(world.sent_log) == 1
    assert app.db.scalar("SELECT state FROM outbox") == "sent"
    assert app.db.scalar("SELECT result FROM delivery_attempts") == "sent"
    assert "reconciled" in app.db.scalar("SELECT error FROM delivery_attempts")


def test_health_reports_browser_state(site):
    world, _, web = site
    web.whoami()
    h = web.health()
    assert h["state"] == "ready" and h["ok"] and "url" in h


def test_instagram_message_reader_on_a_page_shaped_like_instagram():
    """The Instagram reader against a static page with the live layout: inbox list and conversation in one
    main[role=main], composer under the conversation, status lines that aren't messages."""
    from playwright.sync_api import sync_playwright
    from agent.platform.sites import INSTAGRAM
    bubble = '<div style="display:flex;justify-content:{side}"><div style="max-width:60%"><span dir="auto">{t}</span></div></div>'
    html = f"""<body style="margin:0;width:1400px"><main role="main" style="display:flex;margin-left:72px">
      <div aria-label="Thread list" style="width:400px"><div role="button"><span dir="auto">Abby Cook</span>
        <span dir="auto">hey there</span><abbr>2m</abbr></div></div>
      <div style="width:900px;padding-top:100px">
        <div style="text-align:center"><span dir="auto">Today 7:14 PM</span></div>
        {bubble.format(side="flex-start", t="hey how was today?")}
        {bubble.format(side="flex-end", t="Hey im Luke :)")}
        <div style="text-align:right"><span dir="auto">Seen</span></div>
        {bubble.format(side="flex-start", t="10:30 works for me, a really long message that spans quite a lot of the conversation width")}
        <div role="textbox" contenteditable="true" style="margin:20px 40px">&nbsp;</div>
      </div></main></body>"""
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.set_content(html)
        msgs = page.evaluate(INSTAGRAM.read_messages_js())
        browser.close()
    assert [(m["direction"], m["text"][:12]) for m in msgs] == [
        ("in", "hey how was "), ("out", "Hey im Luke "), ("in", "10:30 works ")]
