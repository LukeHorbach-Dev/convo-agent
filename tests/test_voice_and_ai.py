"""Luke's voice on drafted replies, the cached two-part prompt, and the cost estimate."""
from __future__ import annotations

from types import SimpleNamespace

from agent.core.config import LLMConfig
from agent.intel.llm import AnthropicProvider, cost_usd
from agent.intel.voice import in_lukes_voice


def test_voice_pass_matches_how_he_types():
    assert in_lukes_voice("Hey! How's your week going?") == "Hey. Hows your week going ?"
    assert in_lukes_voice("I don't know — maybe tomorrow; we'll see.") == "I dont know, maybe tomorrow, we'll see."
    assert in_lukes_voice("Nice :) see you at 6:30?") == "Nice :) see you at 6:30 ?"
    assert in_lukes_voice("That's gold 😂 and I reckon it's fine") == "Thats gold and i reckon its fine"
    assert in_lukes_voice("Okay so... maybe?") == "Okay so... maybe ?"


def test_voice_pass_runs_on_model_drafts_but_not_on_what_he_types(app, world, llm):
    llm.queue.append({"reply": "Ahh that's cool! What got you into it?", "intent": "continue", "facts_used": [],
                      "needs_human": False, "reason": "", "memory_notes": [], "language": "English",
                      "their_message_english": "I paint", "reply_english": "x"})
    world.new_follower("mia"); app.engine.run_once()
    world.incoming("mia", "I paint")
    for _ in range(3):
        app.engine.run_once()
    sent = [t for u, t in world.sent_log if u == "mia"]
    assert sent[-1] == "Ahh thats cool. What got you into it ?"


def test_prompt_has_a_stable_cacheable_part_and_his_voice(app):
    stable, volatile = app.engine.drafter.system_prompt([], ["okay so what time ?"])
    assert "HOW " in stable and "space before the question mark" in stable
    assert "okay so what time ?" in volatile and "okay so what time" not in stable


def test_anthropic_provider_caches_the_stable_part_and_tracks_cost():
    p = AnthropicProvider(LLMConfig(provider="anthropic", api_key="sk-ant-test"))
    seen = {}
    def create(**kw):
        seen.update(kw)
        return SimpleNamespace(model="claude-opus-5-5", stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text='{"ok": 1}')],
                               usage=SimpleNamespace(input_tokens=700, output_tokens=300, cache_read_input_tokens=2500,
                                                     cache_creation_input_tokens=0))
    p.client = SimpleNamespace(messages=SimpleNamespace(create=create))
    assert p.complete_json(["stable", "volatile"], "user", {"type": "object"}) == {"ok": 1}
    assert seen["system"][0]["cache_control"] == {"type": "ephemeral"} and "cache_control" not in seen["system"][1]
    # 700 x $4 + 300 x $20 + 2,500 x $0.20 per million = about 0.9 of a cent
    assert abs(p.last_cost_usd - 0.0093) < 0.0001


def test_cost_estimate_for_a_typical_reply():
    u = SimpleNamespace(input_tokens=3500, output_tokens=600, cache_read_input_tokens=0, cache_creation_input_tokens=0)
    assert round(cost_usd("claude-opus-5-5", u), 3) == 0.026


def test_notifier_announces_new_waiting_messages_once(world, db, llm):
    from agent.app import build_app
    from agent.core.config import Mode
    from agent.notify import Notifier
    from agent.platform.mock import MockPlatform
    from .conftest import make_settings

    class Realish(MockPlatform):
        is_real = True
    app = build_app(make_settings(mode=Mode.EXECUTE), db=db, platform=Realish(world), world=world, llm=llm)
    banners = []
    n = Notifier(app.db, app.engine.controls, send=lambda t, x: banners.append(x))
    n.check()
    world.new_follower("mia"); world.new_follower("noah"); app.engine.run_once()
    n.check(); n.check()
    assert banners == ["2 messages waiting for your OK"]
    app.engine.controls.pause("Instagram wants you to sign in again", "system")
    n.check()
    assert banners[-1] == "Paused: Instagram wants you to sign in again"
