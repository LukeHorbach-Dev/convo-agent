"""Version 4: overnight half-price writing, Stop now, schedules and quiet hours, the Ask box, person reports,
practice mode (with its automatic switch), and the app's own sign-in, check, health and quit."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent.core.config import LLMConfig
from agent.core.errors import OperatorStop
from agent.intel.llm import AnthropicProvider, FakeLLM
from agent.intel.profile import Profile
from assistant.app import Assistant, create_api
from assistant.ask import Ask, _named, _verbatim, queue_message
from assistant.config import AssistantSettings
from assistant.export import apply_export, read_export
from assistant.reports import briefing, people_csv, person, write_report
from assistant.sender import Sender
from assistant.store import Store
from assistant.writer import FakeBatches, Overnight, RealBatches, estimate_usd
from tests.test_assistant import answer, make_export
from tests.test_eyes import FakeHands, add_draft, chat, run_sender


def settings_for(tmp_path, profile_src=None) -> AssistantSettings:
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "profile.json",
                          never_list_path=tmp_path / "never.txt", downloads_dir=tmp_path)
    s.pace.message_gap_s = s.pace.unfollow_gap_s = 0
    src = Path(__file__).parent / "fixtures" / "profile.json"
    (tmp_path / "profile.json").write_text(src.read_text())
    return s


@pytest.fixture
def loaded(tmp_path):
    store = Store(":memory:")
    apply_export(store, read_export([make_export(tmp_path)]), "lukehorbach2")
    return store, Profile.load(Path(__file__).parent / "fixtures" / "profile.json")


# ------------------------------------------------------------------------------------------------ overnight writing
def test_overnight_batch_writes_placeholders_then_fills_them_through_the_same_checks(loaded):
    store, profile = loaded
    llm = FakeLLM()
    llm.queue.extend([answer("Ahh that's cool! what do you paint?"), answer("Nice photo")])
    batches = FakeBatches(llm, rounds=2)
    o = Overnight(store, llm, batches)
    out = o.submit(profile, set(), window_days=90, min_age=19)
    assert out["queued"] == 2 and out["skipped"] == 2
    writing = store.all("SELECT * FROM drafts WHERE status = 'writing'")
    assert len(writing) == 2 and all("overnight" in d["note"] for d in writing)
    assert o.submit(profile, set(), window_days=90, min_age=19)["queued"] == 0       # nothing written twice
    # The app restarts: a new Overnight picks the batch up from the store.
    o2 = Overnight(store, llm, batches)
    assert o2.check(profile) == "waiting"
    assert o2.check(profile) == "done"
    anna = store.one("SELECT * FROM drafts WHERE thread_id = 'annawalsh_10224'")
    assert anna["status"] == "draft" and anna["text"] == "Ahh thats cool. what do you paint ?"
    assert store.scalar("SELECT COUNT(*) FROM drafts WHERE status = 'writing'") == 0 and o2.pending is None


def test_overnight_failures_become_drafts_for_luke_and_cancel_frees_the_chats(loaded):
    store, profile = loaded
    llm = FakeLLM()
    llm.queue.extend(["timeout", answer("ok")])
    o = Overnight(store, llm, FakeBatches(llm))
    o.submit(profile, set(), window_days=90, min_age=19)
    o.check(profile)
    notes = [d["note"] or "" for d in store.all("SELECT note FROM drafts WHERE kind = 'reply' AND status = 'draft'")]
    assert any("type it yourself" in n for n in notes)
    # cancel
    store.execute("DELETE FROM drafts")
    o.submit(profile, set(), window_days=90, min_age=19)
    assert o.cancel() == 2 and store.scalar("SELECT COUNT(*) FROM drafts WHERE status = 'writing'") == 0


def test_real_batches_are_sent_without_fallbacks_and_cost_half(loaded):
    store, profile = loaded
    sent = {}

    class Msg:
        def __init__(self, text):
            self.content = [SimpleNamespace(type="text", text=text)]
            self.stop_reason, self.model = "end_turn", "claude-opus-5-5"
            self.usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=0, cache_read_input_tokens=0,
                                         cache_creation_input_tokens=0)

    class Client:
        class messages:
            class batches:
                @staticmethod
                def create(requests):
                    sent["requests"] = requests
                    return SimpleNamespace(id="msgbatch_1")

                @staticmethod
                def retrieve(bid):
                    return SimpleNamespace(processing_status="ended")

                @staticmethod
                def results(bid):
                    for r in sent["requests"]:
                        yield SimpleNamespace(custom_id=r["custom_id"], result=SimpleNamespace(
                            type="succeeded", message=Msg(json.dumps(answer("sounds good")))))

    provider = AnthropicProvider(LLMConfig(provider="anthropic", api_key="sk-ant-api03-" + "x" * 30))
    provider.client = Client()
    o = Overnight(store, provider, RealBatches(provider))
    o.submit(profile, set(), window_days=90, min_age=19)
    p = sent["requests"][0]["params"]
    assert "extra_body" not in p and "extra_headers" not in p                  # the batch service takes no fallbacks
    assert p["output_config"]["format"]["type"] == "json_schema" and p["system"][0]["cache_control"]
    assert o.check(profile) == "done"
    assert store.get("ai_spend")["drafts_usd"] == pytest.approx(2 * 4.0 / 2)    # two replies, a million tokens each, half price


def test_fallbacks_only_on_models_that_have_them():
    def params(model):
        return AnthropicProvider(LLMConfig(provider="anthropic", model=model, api_key="sk-ant-api03-" + "x" * 30)).params("s", "u", {})
    assert params("claude-opus-5-5")["extra_body"] == {"fallbacks": "default"}
    assert "extra_body" not in params("claude-haiku-5-5")
    assert estimate_usd("claude-opus-5-5", True) == pytest.approx(estimate_usd("claude-opus-5-5", False) / 2)
    assert estimate_usd("claude-haiku-5-5", True) < 0.001 < estimate_usd("claude-opus-5-5", True) < 0.02


def test_loading_a_download_sends_replies_overnight_by_default(tmp_path):
    s = settings_for(tmp_path)
    make_export(tmp_path)
    llm = FakeLLM()
    llm.queue.extend([answer("hey :)"), answer("nice")])
    a = Assistant(s, store=Store(":memory:"), make_web=lambda: None, llm=llm, notify=lambda *x: None)
    a.practice = SimpleNamespace(new_download=lambda folder: None)        # counts as AI on, without a real key
    assert a.load_download()
    _wait(a)
    # FakeBatches answers at once, so the placeholders are already filled in
    assert a.store.scalar("SELECT COUNT(*) FROM drafts WHERE kind = 'reply' AND status = 'draft' AND text != ''") == 2


def _wait(a):
    for _ in range(200):
        if not a.job["name"]:
            return
        time.sleep(0.02)
    raise AssertionError("job didn't finish")


# ------------------------------------------------------------------------------------------------ stop, schedule, quiet
def test_stop_now_stops_mid_send_and_keeps_the_message_approved(tmp_path):
    store = Store(":memory:")
    add_draft(store, "hello", "a.one", "Hey :)")
    s = settings_for(tmp_path)

    class Slow(FakeHands):
        def open_chat(self, username=None, name=None, known_url=None):
            sender.stop_now()                       # Luke presses Stop now while Claude is opening the chat
            raise OperatorStop("stopped by you")
    sender = Sender(store, s, lambda: Slow(), notify=lambda *a: None, sleep=lambda s: None)
    sender.go()
    sender.step()
    assert not sender.active and "Stopped by you" in sender.status and sender.halt.is_set()
    assert store.one("SELECT status FROM drafts")["status"] == "approved"
    sender.step()                                   # nothing starts again by itself
    assert not sender.active
    sender.go()
    assert not sender.halt.is_set()


@pytest.mark.browser
def test_stop_now_interrupts_claude_between_clicks(tmp_path):
    from agent.core.config import BrowserConfig
    from agent.platform.browser import BrowserSupervisor
    from agent.platform.mock import MockWorld
    from agent.platform.mock_site import SiteServer
    from agent.platform.sites import mock_site_profile
    from assistant.eyes import Hands
    from assistant.practice import StandIn
    from tests.test_eyes import free_port
    world = MockWorld("lukehorbach2")
    world.new_follower("anna")
    server = SiteServer(world, free_port()).start()
    prof = mock_site_profile(server.url)
    ref, presses = {}, {"n": 0}
    h = Hands(BrowserSupervisor(BrowserConfig(headless=True, user_data_dir=tmp_path / "b"), prof), prof, "lukehorbach2",
              StandIn(ref), settle_ms=0)
    ref["hands"] = h
    h.interrupt = lambda: presses.__setitem__("n", presses["n"] + 1) or presses["n"] > 2   # pressed after two checks
    try:
        with pytest.raises(OperatorStop):
            h.open_chat(username="anna")
        assert world.sent_log == []
    finally:
        h.close()
        server.stop()


def test_quiet_hours_and_daily_send_time(tmp_path):
    store = Store(":memory:")
    s = settings_for(tmp_path)
    s.quiet_from, s.quiet_to = "23:00", "08:00"
    sender = Sender(store, s, lambda: FakeHands(), notify=lambda *a: None, sleep=lambda s: None)
    assert sender.quiet_until(datetime(2026, 10, 30, 2, 0)) == "08:00"
    assert sender.quiet_until(datetime(2026, 10, 30, 23, 30)) == "08:00"
    assert sender.quiet_until(datetime(2026, 10, 30, 12, 0)) is None
    s.quiet_from = s.quiet_to = None
    s.send_at = "00:00"
    sender._maybe_scheduled_start()
    assert not sender.active                        # nothing approved: nothing to start
    add_draft(store, "hello", "a.one", "Hey :)")
    store.execute("DELETE FROM state WHERE key = 'scheduled_run'")
    sender._maybe_scheduled_start()
    assert sender.active
    sender.pause()
    sender._maybe_scheduled_start()
    assert not sender.active                        # once a day


# ------------------------------------------------------------------------------------------------ the Ask box
class Script:
    """Plays the AI in the Ask box: a list of turns, each a list of blocks."""

    def __init__(self, turns):
        self.turns, self.calls = list(turns), []
        self.messages = self

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        blocks = self.turns.pop(0)
        return SimpleNamespace(content=blocks, stop_reason="tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn",
                               model="claude-opus-5-5", usage=SimpleNamespace(input_tokens=1000, output_tokens=100,
                                                                              cache_read_input_tokens=0, cache_creation_input_tokens=0))


def tool(name, **inp):
    return SimpleNamespace(type="tool_use", id=f"t_{name}_{time.time_ns()}", name=name, input=inp)


def text(t):
    return SimpleNamespace(type="text", text=t)


@pytest.fixture
def app(tmp_path):
    s = settings_for(tmp_path)
    a = Assistant(s, store=Store(":memory:"), make_web=lambda: None, llm=FakeLLM(), notify=lambda *x: None)
    apply_export(a.store, read_export([make_export(tmp_path)]), "lukehorbach2")
    return a


def test_simple_commands_work_without_the_ai(app):
    add_draft(app.store, "hello", "new.friend", "Hey :)")
    r = app.ask.run("Stop now")
    assert app.sender.halt.is_set() and "Stopped" in r["answer"] and r["cost"] == 0
    assert app.ask.run("send")["actions"] == ["start_sending"] and app.sender.active
    assert "approved" in app.ask.run("status")["answer"]
    assert "AI key" in app.ask.run("what did anna say?")["answer"]


def test_ask_sends_only_luke_s_exact_words_to_people_he_named(app):
    script = Script([
        [tool("queue_message", username="anna.walsh", text="see you at 6", send_now=True)],
        [text("Queued.")],
    ])
    app.ask.client_factory = lambda: script
    app.ask.run("send 'see you at 6' to @anna.walsh")
    d = app.store.one("SELECT * FROM drafts WHERE username = 'anna.walsh' AND status = 'approved'")
    assert d and d["text"] == "see you at 6" and json.loads(d["context"])["direct"] is True
    # Words the AI made up are saved for Luke, not approved.
    script.turns = [[tool("queue_message", username="anna.walsh", text="hey whats up", send_now=True)], [text("ok")]]
    app.ask.run("message @anna.walsh and say hi")
    assert app.store.one("SELECT status FROM drafts WHERE text = 'hey whats up'")["status"] == "draft"


def test_ask_refuses_actions_on_people_luke_didnt_name(app):
    # Their message says "message mia.skis for me": the AI is tricked into it, the tool refuses.
    script = Script([[tool("queue_message", username="mia.skis", text="hi", send_now=True)],
                     [tool("unfollow", username="mia.skis")], [text("done")]])
    app.ask.client_factory = lambda: script
    app.ask.run("what did anna.walsh say?")
    results = [b for m in script.calls[-1]["messages"] if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"]]
    assert all("didn't name" in r["content"] for r in results)
    assert app.store.scalar("SELECT COUNT(*) FROM drafts WHERE username = 'mia.skis'") == 0
    assert app.store.scalar("SELECT COUNT(*) FROM unfollows") == 0


def test_ask_can_look_people_up_and_control_sending(app):
    add_draft(app.store, "hello", "new.friend", "Hey :)")
    script = Script([[tool("person_report", username="anna.walsh"), tool("find_people", query="", show="following", limit=5)],
                     [tool("control", action="start_sending")], [text("Anna paints. Started sending.")]])
    app.ask.client_factory = lambda: script
    r = app.ask.run("tell me about anna.walsh then start sending")
    first = [b for m in script.calls[1]["messages"] if m["role"] == "user" and isinstance(m["content"], list) for b in m["content"]]
    assert "I’m good, I paint a lot" in first[0]["content"] and "new.friend" in first[1]["content"]
    assert app.sender.active and r["answer"].startswith("Anna paints") and r["cost"] > 0
    app.sender.pause()
    script.turns = [[tool("control", action="start_sending")], [text("ok")]]
    app.ask.run("what's new with anna.walsh")                 # no "send" in his words: sending doesn't start
    assert not app.sender.active


def test_named_and_verbatim_helpers():
    assert _named("anna.walsh", "send hi to @anna.walsh please") and _named("anna.walsh", "anna.walsh?")
    assert not _named("anna", "send hi to @anna.walsh") and not _named("", "x")
    assert _verbatim("See you at 6", "send “see you at 6” to anna") and not _verbatim("see you at 7", "see you at 6")


def test_luke_s_own_messages_go_even_into_a_chat_where_he_wrote_last(tmp_path, app):
    queue_message(app.store, set(), "mia.skis", "you free sunday ?", approve=True)     # mia: Luke wrote last
    hands = FakeHands({"mia.skis": chat(username="mia.skis", last_is_mine=True, their_last="hii")})
    run_sender(Sender(app.store, app.s, lambda: hands, notify=lambda *a: None, sleep=lambda s: None))
    assert hands.sent == ["you free sunday ?"]
    assert "never list" in queue_message(app.store, {"nope.person"}, "nope.person", "hi", True)
    assert "rules" in queue_message(app.store, set(), "mia.skis", "send me money on paypal", True)


# ------------------------------------------------------------------------------------------------ reports
def test_person_report_briefing_and_spreadsheet(app):
    p = person(app.store, "@Anna.Walsh", set())
    assert p["chats"][0]["messages"][-1]["text"].startswith("I’m good") and p["never"] is False
    llm = FakeLLM()
    llm.queue.append({"overview": "Anna paints.", "talks_about": ["painting"], "common_ground": [], "open_questions": ["what do you do for fun?"],
                      "careful": [], "next_idea": "i bake a bit, you ?"})
    r = write_report(app.store, llm, app.profile(), "anna.walsh", set())
    assert r["overview"] == "Anna paints." and person(app.store, "anna.walsh", set())["report"]["open_questions"]
    b = briefing(app.store)
    assert b["waiting_on_you"] >= 1 and "top" in b
    csv_text = people_csv(app.store)
    assert csv_text.splitlines()[0].startswith("username,display_name") and "anna.walsh" in csv_text


# ------------------------------------------------------------------------------------------------ practice mode
def test_practice_download_reads_like_a_real_one(tmp_path):
    from agent.platform.mock import MockWorld
    from assistant.practice import seed, write_download
    w = MockWorld("lukehorbach2")
    seed(w)
    path = write_download(w, tmp_path)
    store = Store(":memory:")
    summary = apply_export(store, read_export([path]), "lukehorbach2")
    assert summary["following"] == 10
    assert store.one("SELECT username FROM threads WHERE username = 'maya.paints'")      # chats map to usernames
    again = apply_export(store, read_export([write_download(w, tmp_path)]), "lukehorbach2")
    assert again["new_messages"] == 0                                                  # stable between downloads


@pytest.mark.browser
def test_practice_automatic_mode_writes_approves_and_sends_by_itself(tmp_path):
    from agent.core.config import BrowserConfig
    from agent.platform.browser import BrowserSupervisor
    from assistant.eyes import Hands
    from assistant.practice import PracticeLLM, StandIn, World
    s = settings_for(tmp_path)
    s.downloads_dir = tmp_path / "downloads"
    world = World("lukehorbach2")
    a = None

    def make_web():
        ref = {}
        h = Hands(BrowserSupervisor(BrowserConfig(headless=True, user_data_dir=tmp_path / "b"), world.site), world.site,
                  "lukehorbach2", StandIn(ref), settle_ms=0)
        ref["hands"] = h
        return h
    a = Assistant(s, store=Store(":memory:"), make_web=make_web, llm=PracticeLLM(), notify=lambda *x: None, practice=world)
    a.auto_send = True
    try:
        assert a.load_download()
        _wait(a)
        st = a.store
        # Hard rules held even in automatic mode: the 16 year old and the money ask weren't approved.
        assert st.one("SELECT status FROM drafts WHERE username = 'young.ella'")["status"] == "skipped"
        assert st.one("SELECT status FROM drafts WHERE username = 'cash.quick'")["status"] == "draft"
        assert a.sender.active
        for _ in range(40):
            if not a.sender.active:
                break
            a.sender.step()
        sent_to = {u for u, _ in world.world.sent_log}
        assert {"maya.paints", "tom.kicks", "noah.films"} <= sent_to and not sent_to & {"young.ella", "cash.quick"}
    finally:
        if a.sender.web:
            a.sender.web.close()
        world.stop()


# ------------------------------------------------------------------------------------------------ the API
H = {"X-Agent-Token": "tok"}


def test_api_health_stop_ask_people_and_settings(app, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    c = TestClient(create_api(app, "tok"), base_url="http://127.0.0.1")
    assert c.get("/api/health").json()["app"] == "convo"
    assert c.post("/api/stop", headers=H).status_code == 200 and app.sender.halt.is_set()
    assert c.post("/api/ask", headers=H, json={"text": "status"}).json()["answer"]
    assert c.get("/api/people/anna.walsh", headers=H).json()["chats"]
    assert c.get("/api/people/nobody", headers=H).status_code == 404
    assert "anna.walsh" in c.get("/api/people.csv", headers=H).text
    assert c.post("/api/people/anna.walsh/never", headers=H, json={"on": True}).status_code == 200
    assert "anna.walsh" in app.s.never_list_path.read_text()
    assert c.post("/api/message", headers=H, json={"username": "anna.walsh", "text": "hi"}).status_code == 400   # never list
    assert c.post("/api/message", headers=H, json={"username": "mia.skis", "text": "hey :)"}).status_code == 200
    assert c.post("/api/settings", headers=H, json={"send_at": "9am"}).status_code == 400
    assert c.post("/api/settings", headers=H, json={"send_at": "09:00", "quiet_from": "23:00", "quiet_to": "08:00",
                                                    "writer_model": "claude-sonnet-5-5", "write_overnight": False}).status_code == 200
    assert app.s.send_at == "09:00" and app.s.llm.model == "claude-sonnet-5-5" and not app.s.write_overnight
    assert "AGENT_SEND_AT=09:00" in (tmp_path / ".env").read_text()
    assert c.post("/api/settings", headers=H, json={"send_at": ""}).status_code == 200
    assert "AGENT_SEND_AT" not in (tmp_path / ".env").read_text() and app.s.send_at is None
    assert c.post("/api/settings", headers=H, json={"auto_send": True}).status_code == 400   # practice only
    st = c.get("/api/state", headers=H).json()
    assert st["settings"]["quiet_from"] == "23:00" and "briefing" in st and st["ai"]["estimates"]["claude-haiku-5-5"]
    assert c.post("/api/quit", headers=H).status_code == 200


def test_sign_in_and_check_run_on_the_sender_s_browser(app, tmp_path):
    class Web(FakeHands):
        me = "lukehorbach2"
        last = None

        def open_login(self):
            self.opened = True

        def looks_signed_in(self):
            return True
    web = Web()
    app.sender.make_web = lambda: web
    c = TestClient(create_api(app, "tok"), base_url="http://127.0.0.1")
    import threading
    t = threading.Thread(target=lambda: [app.sender._run_tasks() or time.sleep(0.05) for _ in range(40)])
    t.start()
    assert c.post("/api/instagram/login", headers=H).json()["ok"] and web.opened
    assert c.post("/api/instagram/login/done", headers=H).json()["signed_in"] is True
    t.join()
