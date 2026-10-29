"""Dashboard/API: authentication, host guard, controls, approvals, and diagnostic redaction."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from agent.app import build_app
from agent.core.config import Mode
from agent.platform.mock import MockPlatform
from agent.web.dashboard import create_dashboard

from .conftest import make_settings, settle

TOKEN = "t0ken"
H = {"X-Agent-Token": TOKEN}


class RealishPlatform(MockPlatform):
    is_real = True


@pytest.fixture
def client(app):
    return TestClient(create_dashboard(app, TOKEN), base_url="http://127.0.0.1")


def test_requires_token(client):
    assert client.get("/api/status").status_code == 401
    assert client.get("/api/status", headers={"X-Agent-Token": "wrong"}).status_code == 401
    assert client.get("/api/status", headers=H).status_code == 200


def test_rejects_foreign_host_header(app):
    c = TestClient(create_dashboard(app, TOKEN), base_url="http://evil.example")
    assert c.get("/api/status", headers=H).status_code == 403


def test_state_changes_need_the_header_not_a_cookie(client):
    # A cross-site form post can't add custom headers: without the header it's refused.
    assert client.post("/api/control/emergency-stop").status_code == 401


def test_status_shows_everything_the_operator_needs(client, app, world):
    world.new_follower("mia"); world.new_follower("rae"); world.faults.no_message_button.add("rae")
    app.engine.run_once()
    s = client.get("/api/status", headers=H).json()
    for key in ("run_state", "circuit", "queue_depth", "active_jobs", "drafts", "outbox", "follower_events",
                "incoming", "skipped", "recovery", "errors", "journal", "metrics"):
        assert key in s
    assert any(r["subject_key"] == "rae" and "not permitted" in r["reason"] for r in s["skipped"])
    assert s["outbox"][0]["state"] == "sent"


def test_status_explains_accounts_never_contacted(client, app, world):
    world.new_follower("kai_2010")
    app.engine.run_once()
    nc = client.get("/api/status", headers=H).json()["not_contacted"]
    assert nc == [{"subject_key": "kai_2010", "state": "not contacted", "reason": "username suggests they may be under the minimum age"}]


def test_controls_round_trip(client, app):
    assert client.post("/api/control/pause", headers=H).json()["run_state"] == "paused"
    assert client.post("/api/control/resume", headers=H).json()["run_state"] == "running"
    client.post("/api/control/emergency-stop", headers=H)
    assert client.post("/api/control/resume", headers=H).status_code == 409        # must clear e-stop explicitly
    client.post("/api/control/clear-emergency-stop", headers=H)
    assert client.post("/api/control/resume", headers=H).json()["run_state"] == "running"
    assert app.db.scalar("SELECT COUNT(*) FROM audit WHERE actor = 'operator'") >= 4


def test_approve_flow_on_real_platform(world, llm, db):
    app = build_app(make_settings(mode=Mode.EXECUTE), db=db, platform=RealishPlatform(world), world=world, llm=llm)
    c = TestClient(create_dashboard(app, TOKEN), base_url="http://127.0.0.1")
    world.new_follower("mia")
    app.engine.run_once()
    s = c.get("/api/status", headers=H).json()
    assert s["approval_required"] is True and s["drafts"][0]["job_state"] == "awaiting_approval"
    did = s["drafts"][0]["id"]
    assert c.post(f"/api/drafts/{did}/approve", headers=H, json={"text": "Hey!! where are you from"}).status_code == 409
    assert c.post(f"/api/drafts/{did}/approve", headers=H, json={"text": "Hey im Luke :)"}).status_code == 200
    settle(app.engine, 2)
    assert world.sent_log == [("mia", "Hey im Luke :)")]


def test_resolve_unknown_delivery_via_api(client, app, world, monkeypatch):
    from agent.platform.base import Capabilities
    monkeypatch.setattr(app.platform, "capabilities", lambda: Capabilities("verified", True, True, "best_effort", "verified"))
    monkeypatch.setattr(app.platform, "find_sent", lambda *a: "absent")
    world.new_follower("mia"); world.faults.send_mode = "unknown_delivered"
    settle(app.engine, 5)
    ob = client.get("/api/status", headers=H).json()["outbox"][0]
    assert ob["state"] == "unknown"
    assert client.post(f"/api/outbox/{ob['id']}/resolve", headers=H, json={"delivered": True}).status_code == 200
    assert app.db.scalar("SELECT state FROM jobs") == "completed"
    assert len(world.sent_log) == 1


def test_diagnostics_are_sanitized(app, world):
    app.settings.llm.api_key = SecretStr("sk-ant-supersecret")
    c = TestClient(create_dashboard(app, TOKEN), base_url="http://127.0.0.1")
    world.new_follower("mia"); app.engine.run_once()
    world.incoming("mia", "my private message about my day")
    app.engine.run_once()
    text = c.get("/api/diagnostics", headers=H).text
    assert "supersecret" not in text and "private message" not in text
    assert '"api_key":"[set]"' in text.replace(" ", "")


def test_memory_export_and_delete(client, app, world):
    world.new_follower("mia"); app.engine.run_once()
    world.incoming("mia", "I just adopted a cat"); app.engine.run_once()
    mem = client.get("/api/memory/mia", headers=H).json()["memory"]
    assert any(m["kind"] == "their_statement" and m["source"].startswith("message:") for m in mem)
    assert client.delete("/api/memory/mia", headers=H).json()["deleted"] >= 1


def test_redaction():
    from agent.core.observability import redact
    s = redact("key sk-ant-abc123 sessionid=XYZ password: hunter2")
    assert "abc123" not in s and "XYZ" not in s and "hunter2" not in s


def test_reject_on_a_job_not_awaiting_a_decision_is_409(client, app, world):
    world.new_follower("mia"); app.engine.run_once()                 # mock: auto-sent, job completed
    did = app.db.scalar("SELECT id FROM drafts")
    assert client.post(f"/api/drafts/{did}/reject", headers=H).status_code == 409


def test_send_all_hellos_approves_only_the_hellos_shown(world, llm, db):
    """One click sends the hellos the operator was looking at; ids not on the page, or not hellos, are refused."""
    app = build_app(make_settings(mode=Mode.EXECUTE), db=db, platform=RealishPlatform(world), world=world, llm=llm)
    c = TestClient(create_dashboard(app, TOKEN), base_url="http://127.0.0.1")
    world.new_follower("mia"); world.new_follower("noah"); world.new_follower("zoe")
    app.engine.run_once()
    ids = {d["target"]: d["id"] for d in c.get("/api/status", headers=H).json()["drafts"]}
    r = c.post("/api/drafts/approve-all", headers=H, json={"ids": [ids["mia"], ids["noah"], "drf_not_real"]}).json()
    assert (r["approved"], r["refused"]) == (2, 1)
    settle(app.engine, 3)
    assert sorted(u for u, _ in world.sent_log) == ["mia", "noah"]          # zoe wasn't shown/approved


def test_unfollow_everyone_needs_the_typed_confirmation(world, llm, db):
    s = make_settings(mode=Mode.EXECUTE); s.policy.audience = "following"
    app = build_app(s, db=db, platform=RealishPlatform(world), world=world, llm=llm)
    c = TestClient(create_dashboard(app, TOKEN), base_url="http://127.0.0.1")
    for n in ("mia", "noah"):
        world.new_follower(n)
    app.engine.run_once()
    assert c.get("/api/following?q=mi", headers=H).json()["total"] == 1
    assert c.post("/api/unfollow", headers=H, json={"everyone": True, "confirm": "yes"}).status_code == 400
    r = c.post("/api/unfollow", headers=H, json={"everyone": True, "confirm": "UNFOLLOW EVERYONE"}).json()
    assert r["queued"] == 2
    # their hellos that weren't sent are cancelled, never sent afterwards
    settle(app.engine, 3)
    assert world.sent_log == [] and world.unfollowed == {"mia", "noah"}


def test_ai_key_is_saved_locally_and_switches_replies_on(world, llm, db, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    app = build_app(make_settings(mode=Mode.EXECUTE), db=db, platform=RealishPlatform(world), world=world, llm=llm)
    c = TestClient(create_dashboard(app, TOKEN), base_url="http://127.0.0.1")
    assert c.post("/api/settings/ai-key", headers=H, json={"key": "not-a-key"}).status_code == 400
    key = "sk-ant-api03-" + "x" * 40
    assert c.post("/api/settings/ai-key", headers=H, json={"key": key}).status_code == 200
    env = (tmp_path / ".env")
    assert f"AGENT_LLM__API_KEY={key}" in env.read_text() and oct(env.stat().st_mode & 0o777) == "0o600"
    s = c.get("/api/status", headers=H).json()
    assert s["ai"] == {"provider": "anthropic", "manual_replies": False}
    assert key not in c.get("/api/diagnostics", headers=H).text
