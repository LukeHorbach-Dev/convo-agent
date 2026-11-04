"""Updates from GitHub: finds the right release, installs only what checks out, keeps the old version for Undo,
and never touches the data."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from assistant import __version__
from assistant.updates import UpdateError, Updates, changelog_notes, demo_tarball, parse_version


def test_versions_sort_previews_before_the_finished_version():
    assert parse_version("5.0.0") > parse_version("5.0.0-preview.2") > parse_version("5.0.0-preview.1") > parse_version("4.1.1")
    assert parse_version("v5.0.1") > parse_version("5.0.0")
    assert parse_version("nonsense") < parse_version("0.0.1")


class FakeGitHub:
    """api.github.com for one private project, with a key that must be right."""

    def __init__(self, releases, key="github_pat_" + "a" * 30):
        self.releases, self.key, self.files, self.seen = releases, key, {}, []

    def add(self, version, prerelease=False, tar=None, sums=None):
        tar = tar if tar is not None else demo_tarball(version)
        name = f"convo-{version}.tar.gz"
        self.files[f"/assets/{name}"] = tar
        self.files["/assets/sums-" + version] = (sums or f"{hashlib.sha256(tar).hexdigest()}  {name}\n").encode()
        self.releases.append({"tag_name": f"v{version}", "prerelease": prerelease, "draft": False, "body": f"notes for {version}",
                              "html_url": "https://github.com/x", "published_at": "2026-11-04T10:00:00Z",
                              "assets": [{"name": name, "url": f"https://api.github.com/assets/{name}", "size": len(tar)},
                                         {"name": "SHA256SUMS.txt", "url": f"https://api.github.com/assets/sums-{version}", "size": 1}]})

    def handler(self, req: httpx.Request) -> httpx.Response:
        self.seen.append((req.url.host, req.headers.get("authorization")))
        if req.headers.get("authorization") != f"Bearer {self.key}":
            return httpx.Response(404, json={"message": "Not Found"})
        if req.url.path.endswith("/releases"):
            return httpx.Response(200, json=self.releases)
        if req.url.path in self.files:
            return httpx.Response(200, content=self.files[req.url.path])
        return httpx.Response(404)


def make(tmp_path, gh, token=None, current="5.0.0-preview.1", ok_start=True, **kw):
    calls = []

    def run(cmd, **k):
        calls.append(cmd)
        if "-c" in cmd:          # the "does it start" check prints the version it found
            v = (Path(k["env"]["PYTHONPATH"]) / "VERSION").read_text().strip()
            return subprocess.CompletedProcess(cmd, 0 if ok_start else 1, stdout=v + "\n", stderr="boom")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    venv = tmp_path / "home" / "venv" / "bin"
    venv.mkdir(parents=True)
    restarted, backups, logs = [], [], []
    u = Updates(tmp_path / "home", token=lambda: token, current=current, http=httpx.Client(transport=httpx.MockTransport(gh.handler)),
                log=lambda k, t: logs.append(t), backup=backups.append, restart=restarted.append, python=str(venv / "python"),
                run=run, **kw)
    return u, calls, restarted, backups, logs


def test_without_the_key_it_says_what_to_do(tmp_path):
    gh = FakeGitHub([])
    gh.add("5.0.0")
    u, *_ = make(tmp_path, gh, token=None)
    s = u.check()
    assert s["status"] == "error" and "GitHub key" in s["error"] and not s["available"]


def test_a_wrong_key_is_explained(tmp_path):
    gh = FakeGitHub([])
    u, *_ = make(tmp_path, gh, token="github_pat_" + "b" * 30)
    assert "Contents: Read-only" in u.check()["error"]


def test_finds_the_newest_release_and_previews_only_when_wanted(tmp_path):
    key = "github_pat_" + "a" * 30
    gh = FakeGitHub([])
    gh.add("4.1.1")
    gh.add("5.0.0")
    gh.add("5.1.0-preview.1", prerelease=True)
    u, *_ = make(tmp_path, gh, token=key, current="5.0.0")
    s = u.check()
    assert s["available"] is False and s["status"] == "up_to_date"             # finished versions only by default
    u.previews = lambda: True
    assert u.check()["latest"]["version"] == "5.1.0-preview.1"
    u2, *_ = make(tmp_path / "b", gh, token=key, current="5.0.0-preview.1")     # on a preview: previews are offered
    s2 = u2.check()
    assert s2["available"] and s2["latest"]["version"] == "5.1.0-preview.1"


def test_install_switches_keeps_the_old_one_and_backs_up_first(tmp_path):
    key = "github_pat_" + "a" * 30
    gh = FakeGitHub([])
    gh.add("5.0.0")
    u, calls, restarted, backups, logs = make(tmp_path, gh, token=key)
    old = tmp_path / "home" / "versions" / "5.0.0-preview.1"
    old.mkdir(parents=True)
    (old / "VERSION").write_text("5.0.0-preview.1\n")
    u.check()
    target = u.install(u.state["latest"])
    home = tmp_path / "home"
    assert target == home / "versions" / "5.0.0" and (target / "VERSION").read_text().strip() == "5.0.0"
    assert (home / "current").read_text().strip() == "5.0.0" and (home / "previous").read_text().strip() == "5.0.0-preview.1"
    assert (home / ".installed").read_text().strip() == "5.0.0"
    assert backups == ["before-update-5.0.0"] and restarted == [target]
    assert any("pip" in c for c in calls)                                      # new parts installed (lock changed)
    assert (home / "venv" / ".convo-lock").exists()
    assert all(host == "api.github.com" for host, _ in gh.seen)                # the key only goes to GitHub
    # A second install with the same parts list skips the installer.
    calls.clear()
    gh.add("5.0.1")
    u.current = "5.0.0"
    u.check()
    u.install(u.state["latest"])
    assert not any("pip" in c for c in calls)
    # Undo goes back one.
    u.current = "5.0.1"
    u.undo()
    assert (home / "current").read_text().strip() == "5.0.0"


def test_a_damaged_download_changes_nothing(tmp_path):
    key = "github_pat_" + "a" * 30
    gh = FakeGitHub([])
    gh.add("5.0.0", sums="0" * 64 + "  convo-5.0.0.tar.gz\n")
    u, calls, restarted, backups, _ = make(tmp_path, gh, token=key)
    u.check()
    with pytest.raises(UpdateError, match="checksum"):
        u.install(u.state["latest"])
    home = tmp_path / "home"
    assert not (home / "current").exists() and not (home / "versions" / "5.0.0").exists() and backups == [] and restarted == []


def test_a_version_that_does_not_start_is_not_switched_to(tmp_path):
    key = "github_pat_" + "a" * 30
    gh = FakeGitHub([])
    gh.add("5.0.0")
    u, calls, restarted, backups, _ = make(tmp_path, gh, token=key, ok_start=False)
    u.check()
    with pytest.raises(UpdateError, match="didn't start"):
        u.install(u.state["latest"])
    assert not (tmp_path / "home" / "current").exists() and restarted == [] and backups == []


def test_install_in_the_background_reports_problems_instead_of_hanging(tmp_path):
    key = "github_pat_" + "a" * 30
    gh = FakeGitHub([])
    gh.add("5.0.0", sums="0" * 64 + "  convo-5.0.0.tar.gz\n")
    u, *_ = make(tmp_path, gh, token=key)
    u.check()
    u.start_install()
    import time
    for _ in range(50):
        if u.state["status"] == "error":
            break
        time.sleep(0.05)
    assert u.state["status"] == "error" and "checksum" in u.state["error"]


def test_a_copy_run_from_its_code_folder_does_not_install(tmp_path):
    u = Updates(None, token=lambda: None)
    assert u.summary()["can_install"] is False and "git" in u.summary()["why_not"]
    with pytest.raises(UpdateError):
        u.start_install()


def test_whats_new_comes_from_the_changelog():
    assert changelog_notes(__version__), "CHANGELOG.md needs a section for this version"
    assert changelog_notes("0.0.0-never") is None


def test_settings_save_the_github_key_owner_only(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from assistant.app import Assistant, create_api
    from assistant.config import AssistantSettings
    from assistant.practice import PracticeLLM
    from assistant.store import Store
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "p.json", never_list_path=tmp_path / "n.txt")
    a = Assistant(s, store=Store(tmp_path / "a.db"), make_web=lambda: None, llm=PracticeLLM(), notify=lambda *x: None,
                  env_path=tmp_path / ".env")
    c = TestClient(create_api(a, "t"), base_url="http://127.0.0.1")
    h = {"X-Agent-Token": "t"}
    assert c.post("/api/settings", json={"github_token": "hello"}, headers=h).status_code == 400
    key = "github_pat_" + "A1_" * 10
    assert c.post("/api/settings", json={"github_token": key}, headers=h).status_code == 200
    env = (tmp_path / ".env").read_text()
    assert f"AGENT_GITHUB_TOKEN={key}" in env and oct((tmp_path / ".env").stat().st_mode)[-3:] == "600"
    body = c.get("/api/updates", headers=h).json()
    assert body["connected"] is True and key not in json.dumps(body)              # the key never comes back out
    assert key not in json.dumps(c.get("/api/state", headers=h).json())
    assert c.post("/api/settings", json={"github_token": ""}, headers=h).status_code == 200
    assert "AGENT_GITHUB_TOKEN" not in (tmp_path / ".env").read_text()
    assert c.post("/api/updates/install", headers=h).status_code == 400            # nothing to install, said plainly


def test_the_mac_window_falls_back_to_chromium_after_a_crash(tmp_path, monkeypatch):
    from assistant import window
    monkeypatch.delenv("CONVO_WINDOW", raising=False)
    monkeypatch.setattr(window.sys, "platform", "darwin")
    assert window.window_mode(tmp_path, "5.0.0") == "native"
    (tmp_path / "window").mkdir()
    (tmp_path / "window" / "native-trying").write_text("5.0.0")         # the last one died before it showed
    assert window.window_mode(tmp_path, "5.0.0") == "chromium"
    assert window.window_mode(tmp_path, "5.0.0") == "chromium"           # and stays on Chromium for this version
    assert window.window_mode(tmp_path, "5.0.1") == "native"             # a new version tries the Mac window again
    monkeypatch.setenv("CONVO_WINDOW", "chromium")
    assert window.window_mode(tmp_path, "5.0.1") == "chromium"
    monkeypatch.setattr(window.sys, "platform", "linux")
    monkeypatch.delenv("CONVO_WINDOW")
    assert window.window_mode(tmp_path, "5.0.1") == "chromium"
