"""Version 3's website layer: Claude looks at the page and clicks (assistant/eyes.py).

The real model isn't called here. A stand-in ("Robot") plays Claude's part against the practice site in a real
Chromium: it decides from the page what a person would click and answers with the same tool calls Claude makes.
That tests everything around the model: the loop, the clicks landing, what Claude is and isn't allowed to do, the
checks on its reports, typing and sending only the approved text, and the sender's decisions.
"""
from __future__ import annotations

import socket
from itertools import count
from types import SimpleNamespace

import pytest

from agent.core.config import BrowserConfig
from agent.core.errors import AmbiguousState, InterventionRequired, PermissionRestricted, RateLimited
from agent.platform.browser import BrowserSupervisor
from agent.platform.mock import MockWorld
from agent.platform.mock_site import SiteServer
from agent.platform.sites import mock_site_profile
from assistant.config import AssistantSettings
from assistant.eyes import KEYS, TOOLSET, ChatView, Hands
from assistant.sender import Sender, similar
from assistant.store import Store

ids = count(1)


def use(name, inp=None, computer=True):
    return SimpleNamespace(type="tool_use", id=f"toolu_{next(ids)}", name=name, input=inp or {},
                           toolset_name="computer" if computer else None)


def center(page, selector):
    box = page.locator(selector).filter(visible=True).first.bounding_box()
    return [int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)]


class Robot:
    """Plays Claude: looks at the page (through the DOM, as a stand-in for the screenshot) and answers."""

    def __init__(self, hands_ref, misbehave=None):
        self.hands_ref = hands_ref
        self.calls = []
        self.misbehave = misbehave or []          # actions to try first (e.g. typing), to check they're refused
        self.messages = self

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})   # as it was at this call
        if len(kw["messages"]) == 1:              # a new task
            self.unfollowed = False
        page = self.hands_ref["hands"].page
        task = kw["messages"][0]["content"][0]["text"]
        if self.misbehave:
            blocks = [self.misbehave.pop(0)]
        else:
            blocks = self.decide(page, task)
        return SimpleNamespace(content=blocks, stop_reason="tool_use", model="claude-haiku-5-5",
                               usage=SimpleNamespace(input_tokens=3000, output_tokens=200, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))

    def decide(self, page, task):
        page.wait_for_timeout(450)                # let the practice profile draw its buttons
        path = page.url.split("//", 1)[1].split("/", 1)[1]
        vis = lambda sel: page.locator(sel).filter(visible=True).count() > 0
        if task.startswith("Which Instagram account"):
            return [use("signed_in_as", {"username": "lukehorbach2" if vis(':text("Edit profile")') else ""}, False)]
        if task.startswith("Only look") and "chat list" in task:
            row = page.locator('[data-testid="thread-row"]').first.inner_text().split(" ")[0]
            return [use("top_chat", {"name": row}, False)]
        if task.startswith("Only look"):
            return [use("follow_state", {"i_follow": vis(':text-is("Following")')}, False)]
        if task.startswith("Unfollow"):
            if vis('[role=dialog] :text-is("Unfollow")'):
                self.unfollowed = True
                return [use("left_click", {"coordinate": center(page, '[role=dialog] :text-is("Unfollow")')}), use("screenshot")]
            if vis('#fb:text-is("Following")'):
                return [use("left_click", {"coordinate": center(page, "#fb")}), use("screenshot")]
            return [use("unfollow_finished", {"result": "unfollowed" if self.unfollowed else "was_not_following"}, False)]
        # Opening a chat.
        if path.startswith("direct/t/"):
            if not page.evaluate("() => document.activeElement && document.activeElement.isContentEditable"):
                return [use("left_click", {"coordinate": center(page, "[role=textbox]")}), use("screenshot")]
            msgs = page.evaluate("""() => [...document.querySelectorAll('[data-testid=message]')]
                                    .map(m => ({d: m.dataset.direction, t: m.textContent}))""")
            theirs = [m["t"] for m in msgs if m["d"] == "in"]
            user = page.locator('[data-testid="thread-username"]').first.inner_text()
            return [use("chat_is_open", {"username_shown": user, "name_shown": user, "chat_is_empty": not msgs,
                                         "last_message_is_mine": bool(msgs) and msgs[-1]["d"] == "out",
                                         "their_last_message": theirs[-1] if theirs else "",
                                         "their_recent_messages": theirs[-5:]}, False)]
        if path.startswith("direct/inbox"):
            name = task.split('"')[1]
            row = f'[data-testid="thread-row"]:text-matches("^{name} ")'
            if not vis(row):
                return [use("cannot_continue", {"reason": "not_found", "explanation": "no chat with that name"}, False)]
            return [use("left_click", {"coordinate": center(page, row)}), use("screenshot")]
        if vis('svg[aria-label="Expand"]'):
            return [use("left_click", {"coordinate": center(page, 'svg[aria-label="Expand"]')}), use("screenshot")]
        if vis('[data-testid="message-button"]'):
            return [use("left_click", {"coordinate": center(page, '[data-testid="message-button"]')}), use("screenshot")]
        if vis(':text("this page isn\'t available")'):
            return [use("cannot_continue", {"reason": "profile_unavailable", "explanation": "page isn't available"}, False)]
        return [use("go_to", {"where": "message_link"}, False)]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def site(tmp_path):
    world = MockWorld("lukehorbach2")
    server = SiteServer(world, free_port()).start()
    prof = mock_site_profile(server.url)
    cfg = BrowserConfig(headless=True, user_data_dir=tmp_path / "browser", navigation_timeout_ms=5000,
                        action_timeout_ms=3000, health_check_timeout_ms=2000, max_recoveries_per_run=2)
    made = []

    def make_hands(misbehave=None, spend=None):
        ref = {}
        robot = Robot(ref, misbehave)
        h = Hands(BrowserSupervisor(cfg, prof), prof, "lukehorbach2", robot, settle_ms=0,
                  on_cost=(lambda usd: spend.append(usd)) if spend is not None else None)
        ref["hands"] = h
        h.robot = robot
        made.append(h)
        return h
    yield world, server, make_hands
    for h in made:
        h.close()
    server.stop()


def settings_for(tmp_path) -> AssistantSettings:
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "profile.json",
                          never_list_path=tmp_path / "never.txt", downloads_dir=tmp_path)
    s.pace.message_gap_s = s.pace.unfollow_gap_s = 0
    return s


def add_draft(store, kind, username, text, replying_to=None, status="approved", other_name=None):
    store.execute("INSERT INTO drafts(kind, username, thread_id, other_name, replying_to, replying_at, text, status, created_at, updated_at) "
                  "VALUES (?, ?, ?, ?, ?, 1, ?, ?, 'x', 'x')", (kind, username, f"t_{username or other_name}" if kind == "reply" else None,
                                                              other_name or username, replying_to, text, status))
    return store.scalar("SELECT MAX(id) FROM drafts")


def run_sender(sender, steps=10):
    sender.go()
    for _ in range(steps):
        if not sender.active:
            break
        sender.step()


# ------------------------------------------------------------------------------------------------ the loop
def test_claude_can_only_look_click_scroll_and_move_around():
    on = {m for m, c in TOOLSET["configs"].items() if c["enabled"]}
    assert on == {"screenshot", "left_click", "scroll", "key", "mouse_move", "wait"}
    assert "type" not in on and "double_click" not in on            # no typing; a double click likes a message
    assert not any(v in ("Enter", "Return") for v in KEYS.values())


@pytest.mark.browser
def test_claude_opens_a_chat_from_the_profile_and_the_app_sends_exactly_the_approved_text(site):
    world, _, make_hands = site
    world.new_follower("anna")
    world.incoming("anna", "what do you do for fun?")
    spend = []
    h = make_hands(spend=spend)
    chat = h.open_chat(username="anna")
    assert chat.username == "anna" and not chat.empty and not chat.last_is_mine
    assert chat.their_last == "what do you do for fun?"
    h.send_here("Not much, pottery mostly. You ?")
    assert world.sent_log == [("anna", "Not much, pottery mostly. You ?")]
    # Every computer result echoes the toolset name; screenshots are the page at 1280x800.
    last = h.robot.calls[-1]
    results = [b for m in last["messages"] if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"]
               if isinstance(b, dict) and b.get("type") == "tool_result" and "toolset_name" in b]
    assert results and all(r["toolset_name"] == "computer" for r in results)
    assert h.page.viewport_size == {"width": 1280, "height": 800}
    assert spend and all(c > 0 for c in spend)


@pytest.mark.browser
def test_typing_and_enter_are_refused_so_claude_cant_send_anything(site):
    world, _, make_hands = site
    world.new_follower("anna")
    h = make_hands(misbehave=[use("type", {"text": "hi from claude"}), use("key", {"text": "Return"})])
    h.open_chat(username="anna")
    results = [b for m in h.robot.calls[2]["messages"] if m["role"] == "user" and isinstance(m["content"], list)
               for b in m["content"] if isinstance(b, dict) and b.get("type") == "tool_result"]
    texts = " ".join(c["text"] for r in results for c in (r["content"] if isinstance(r["content"], list) else []) if c.get("type") == "text")
    assert "isn't available" in texts and "can't type or press Enter" in texts
    assert world.sent_log == []


@pytest.mark.browser
def test_a_report_from_the_wrong_page_is_sent_back_to_look_again(site):
    world, _, make_hands = site
    world.new_follower("anna")
    early = use("chat_is_open", {"username_shown": "anna", "name_shown": "anna", "chat_is_empty": True,
                                 "last_message_is_mine": False, "their_last_message": "", "their_recent_messages": []}, False)
    h = make_hands(misbehave=[early])
    chat = h.open_chat(username="anna")                 # the early report was refused; the robot then did it properly
    assert chat.username == "anna"
    assert any("not accepted" in line for line in h.last.log)


@pytest.mark.browser
def test_a_chat_is_found_by_name_in_the_inbox(site):
    world, _, make_hands = site
    world.new_follower("bea")
    world.incoming("bea", "hello there")
    h = make_hands()
    chat = h.open_chat(name="bea")
    assert chat.username == "bea" and chat.their_last == "hello there"


@pytest.mark.browser
def test_unfollow_and_profile_problems(site):
    world, _, make_hands = site
    world.new_follower("cara")
    h = make_hands()
    assert h.do_i_follow("cara") is True
    assert h.unfollow("cara") == "unfollowed" and "cara" in world.unfollowed
    assert h.unfollow("cara") == "not_following"
    with pytest.raises(PermissionRestricted):
        h.open_chat(username="nobody.here")


@pytest.mark.browser
def test_self_check_passes_on_the_practice_site_and_changes_nothing(site, tmp_path):
    from assistant.check import run_check
    world, _, make_hands = site
    world.new_follower("anna"); world.incoming("anna", "hey")
    store = Store(":memory:")
    store.execute("INSERT INTO people(username, i_follow) VALUES ('anna', 1)")
    store.execute("INSERT INTO threads(id, username, last_at, is_group) VALUES ('anna_1', 'anna', 1, 0)")
    report = run_check(make_hands(), store)
    assert report.startswith("Self-check: 6 of 6 steps passed"), report
    assert world.sent_log == [] and world.unfollowed == set()


# ------------------------------------------------------------------------------------------------ the sender, end to end
@pytest.mark.browser
def test_sender_sends_hellos_and_replies_and_checks_each_chat_first(site, tmp_path):
    world, _, make_hands = site
    for u in ("new.friend", "old.friend", "anna", "busy"):
        world.new_follower(u)
    world.incoming("old.friend", "we already talk")             # a hello must not go into an existing chat
    world.incoming("anna", "what do you do for fun?")
    world.incoming("busy", "first"); world.incoming("busy", "second message")
    store = Store(":memory:")
    h1 = add_draft(store, "hello", "new.friend", "Hey im trying to branch out a little im Luke :)")
    h2 = add_draft(store, "hello", "old.friend", "Hey, hows your week going ?")
    r1 = add_draft(store, "reply", "anna", "Not much, pottery mostly. You ?", replying_to="what do you do for fun?")
    r2 = add_draft(store, "reply", "busy", "ok", replying_to="first")       # they wrote again since
    banners = []
    sender = Sender(store, settings_for(tmp_path), make_hands, notify=lambda t, x: banners.append(x), sleep=lambda s: None)
    run_sender(sender)
    st = {d["id"]: d for d in store.all("SELECT * FROM drafts")}
    assert st[h1]["status"] == "sent" and st[r1]["status"] == "sent", st
    assert st[h2]["status"] == "skipped" and "already have a chat" in st[h2]["note"]
    assert st[r2]["status"] == "draft" and "written again" in st[r2]["note"]
    assert sorted(world.sent_log) == [("anna", "Not much, pottery mostly. You ?"),
                                      ("new.friend", "Hey im trying to branch out a little im Luke :)")]
    assert sender.active is False and banners[-1].startswith("All done")


@pytest.mark.browser
def test_sender_unfollows_and_respects_the_daily_number(site, tmp_path):
    world, _, make_hands = site
    store = Store(":memory:")
    for u in ("a.one", "b.two", "c.three"):
        world.new_follower(u)
        store.execute("INSERT INTO unfollows(username, queued_at) VALUES (?, 'x')", (u,))
    s = settings_for(tmp_path); s.pace.daily_unfollows = 2
    sender = Sender(store, s, make_hands, notify=lambda *a: None, sleep=lambda s: None)
    run_sender(sender)
    assert len(world.unfollowed) == 2
    assert store.scalar("SELECT COUNT(*) FROM unfollows WHERE status = 'queued'") == 1


# ------------------------------------------------------------------------------------------------ the sender's decisions
class FakeHands:
    """The website as the sender sees it, without a browser."""

    def __init__(self, chats=None, problem=None):
        self.chats, self.problem = chats or {}, problem
        self.sent, self.page = [], SimpleNamespace(evaluate=lambda js: "")

    def whoami(self):
        return "lukehorbach2"

    def open_chat(self, username=None, name=None, known_url=None):
        self.known_urls = getattr(self, "known_urls", []) + [known_url]
        if self.problem:
            raise self.problem
        return self.chats[username or name]

    def send_here(self, text):
        self.sent.append(text)

    def save_screenshot(self, label):
        pass

    def close(self):
        pass


def chat(**kw):
    base = dict(username="anna", name="Anna", empty=False, last_is_mine=False, their_last="", their_recent=[])
    return ChatView(**{**base, **kw})


def test_replies_go_only_when_the_chat_still_matches_the_download(tmp_path):
    store = Store(":memory:")
    ok = add_draft(store, "reply", "anna", "haha yes", replying_to="did you see the game?!")
    mine = add_draft(store, "reply", "ben", "x", replying_to="hey")
    other = add_draft(store, "reply", "cat", "x", replying_to="hey")
    hands = FakeHands({"anna": chat(their_last="Did you see the game? 😂", their_recent=["Did you see the game? 😂"]),
                       "ben": chat(username="ben", last_is_mine=True, their_last="hey"),
                       "cat": chat(username="cat", their_last="something else entirely", their_recent=["something else entirely"])})
    sender = Sender(store, settings_for(tmp_path), lambda: hands, notify=lambda *a: None, sleep=lambda s: None)
    run_sender(sender)
    st = {d["id"]: d for d in store.all("SELECT * FROM drafts")}
    assert st[ok]["status"] == "sent" and hands.sent == ["haha yes"]
    assert st[mine]["status"] == "skipped" and st[other]["status"] == "draft" and "doesn't match" in st[other]["note"]


def test_a_chat_opened_by_name_teaches_the_username(tmp_path):
    store = Store(":memory:")
    d = add_draft(store, "reply", None, "ok :)", replying_to="hi", other_name="Thais Yuki")
    hands = FakeHands({"Thais Yuki": chat(username="thyuki.okada", name="Thais Yuki", their_last="hi")})
    run_sender(Sender(store, settings_for(tmp_path), lambda: hands, notify=lambda *a: None, sleep=lambda s: None))
    assert store.one("SELECT username, status FROM drafts WHERE id = ?", (d,)) == {"username": "thyuki.okada", "status": "sent"}


def test_sender_stops_and_says_why_when_pages_keep_going_wrong(tmp_path):
    store = Store(":memory:")
    for u in ("p1", "p2", "p3"):
        add_draft(store, "hello", u, "Hey, hows your week going ?")
    banners = []
    hands = FakeHands(problem=AmbiguousState("x: Claude couldn't find the Message button"))
    sender = Sender(store, settings_for(tmp_path), lambda: hands, notify=lambda t, x: banners.append(x), sleep=lambda s: None)
    run_sender(sender, steps=10)
    assert sender.active is False and sender.status.startswith("Stopped") and "check.sh" in banners[-1]
    assert hands.sent == []


def test_instagram_warnings_mean_a_long_break(tmp_path):
    import time
    store = Store(":memory:")
    add_draft(store, "hello", "x.one", "Hey, hows your week going ?")
    sender = Sender(store, settings_for(tmp_path), lambda: FakeHands(problem=RateLimited("warning")),
                    notify=lambda *a: None, sleep=lambda s: None)
    run_sender(sender, steps=2)
    assert sender.wait_until > time.time() + 5 * 3600
    assert store.one("SELECT status FROM drafts")["status"] == "approved"


def test_without_an_ai_key_sending_stops_and_says_so(tmp_path):
    from agent.platform.sites import INSTAGRAM
    hands = Hands(None, INSTAGRAM, "lukehorbach2", None)           # no AI client: nothing that needs Claude can run
    with pytest.raises(InterventionRequired, match="AI key"):
        hands._task("x", [])
    store = Store(":memory:")
    add_draft(store, "hello", "x.one", "Hey, hows your week going ?")

    class NoKey(FakeHands):
        def whoami(self):
            raise InterventionRequired("Sending needs your AI key, because Claude looks at Instagram's page to do it.")
    sender = Sender(store, settings_for(tmp_path), lambda: NoKey(), notify=lambda *a: None, sleep=lambda s: None)
    run_sender(sender, steps=2)
    assert sender.status.startswith("Stopped") and "AI key" in sender.status
    assert store.one("SELECT status FROM drafts")["status"] == "approved"


def test_messages_are_compared_the_way_claude_copies_them():
    assert similar("did you see the game?!", "Did you see the game? 😂")
    assert similar("what do you do for fun", "what do you do for fun?")
    assert not similar("see you tomorrow", "what do you do for fun?")
    assert similar("😂😂", "😂😂")


# ------------------------------------------------------------------------------------------------ 3.1 refinements
def images_in(call):
    return [c for m in call["messages"] if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"]
            if isinstance(b, dict) and b.get("type") == "tool_result" and isinstance(b.get("content"), list)
            for c in b["content"] if c.get("type") == "image"]


@pytest.mark.browser
def test_a_fresh_picture_comes_back_after_the_last_click_of_a_turn(site):
    world, _, make_hands = site
    world.new_follower("anna")
    h = make_hands(misbehave=[use("left_click", {"coordinate": [5, 5]})])     # a click with no screenshot asked for
    h.open_chat(username="anna")
    assert len(images_in(h.robot.calls[1])) == 1


@pytest.mark.browser
def test_chats_are_remembered_so_the_next_message_opens_directly(site, tmp_path):
    world, _, make_hands = site
    world.new_follower("anna")
    store = Store(":memory:")
    add_draft(store, "hello", "anna", "Hey im Luke :)")
    h = make_hands()
    sender = Sender(store, settings_for(tmp_path), lambda: h, notify=lambda *a: None, sleep=lambda s: None)
    run_sender(sender)
    url = store.one("SELECT chat_url FROM people WHERE username = 'anna'")["chat_url"]
    assert url and "/direct/t/" in url
    world.incoming("anna", "hey :) how are you")
    add_draft(store, "reply", "anna", "good thanks, you ?", replying_to="hey :) how are you")
    before = len(h.robot.calls)
    run_sender(sender)
    assert ("anna", "good thanks, you ?") in world.sent_log
    assert len(h.robot.calls) - before <= 2                    # straight to the chat: click the box, report
    assert any("cents" in r["text"] for r in store.all("SELECT text FROM activity WHERE kind = 'good'"))


@pytest.mark.browser
def test_live_view_gets_each_picture(site):
    world, _, make_hands = site
    world.new_follower("anna")
    seen = []
    h = make_hands()
    h.on_view = lambda png, note: seen.append((png[:8], note))
    h.open_chat(username="anna")
    assert seen and all(p == b"\x89PNG\r\n\x1a\n" for p, _ in seen) and seen[-1][1] in ("Clicking", "Looking at the page")


def test_send_just_one_first_then_pause(tmp_path):
    store = Store(":memory:")
    add_draft(store, "hello", "a.one", "Hey :)")
    add_draft(store, "hello", "b.two", "Hey :)")
    hands = FakeHands({"a.one": chat(username="a.one", empty=True), "b.two": chat(username="b.two", empty=True)})
    sender = Sender(store, settings_for(tmp_path), lambda: hands, notify=lambda *a: None, sleep=lambda s: None)
    sender.go(just_one=True)
    for _ in range(4):
        if sender.active:
            sender.step()
    assert len(hands.sent) == 1 and not sender.active and sender.status.startswith("Sent one")


def test_daily_ai_limit_pauses_sending_until_tomorrow(tmp_path):
    import time
    store = Store(":memory:")
    add_draft(store, "hello", "a.one", "Hey :)")
    store.add_spend(6.0, "web")
    s = settings_for(tmp_path); s.daily_ai_budget_usd = 5.0
    hands = FakeHands({"a.one": chat(username="a.one", empty=True)})
    banners = []
    sender = Sender(store, s, lambda: hands, notify=lambda t, x: banners.append(x), sleep=lambda s: None)
    run_sender(sender, steps=2)
    assert hands.sent == [] and "spending limit" in sender.status and sender.wait_until > time.time()
    assert len(banners) == 1


def test_dashboard_live_view_and_send_one(tmp_path):
    from fastapi.testclient import TestClient
    from agent.intel.llm import FakeLLM
    from assistant.app import Assistant, create_api
    a = Assistant(settings_for(tmp_path), store=Store(":memory:"), make_web=lambda: None, llm=FakeLLM(), notify=lambda *a: None)
    c = TestClient(create_api(a, "tok"), base_url="http://127.0.0.1")
    H = {"X-Agent-Token": "tok"}
    assert c.get("/api/live", headers=H).json()["image"] is None
    a.live.update(png=b"\x89PNGfake", note="Clicking", at=__import__("time").time())
    assert c.get("/api/live", headers=H).json()["note"] == "Clicking"
    assert c.get("/api/live").status_code == 401
    c.post("/api/send/start", headers=H, json={"just_one": True})
    assert a.sender.active and a.sender.just_one
    assert c.post("/api/settings", headers=H, json={"daily_ai_budget_usd": 0.1}).status_code == 400
