"""Every endpoint needs Convo's password, except the few that can't (the page itself, health, the signed media links
and the state-checked sign-in return), and no other host is answered."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from fastapi.testclient import TestClient

from assistant.app import Assistant, create_api
from assistant.config import AssistantSettings
from assistant.practice import PracticeLLM
from assistant.store import Store

OPEN = {"/", "/classic", "/api/health", "/media/{cid}/{role}", "/oauth/{pid}"}


def test_nothing_new_is_open_without_the_password(tmp_path):
    shutil.copy(Path(__file__).parent / "fixtures" / "profile.json", tmp_path / "profile.json")
    s = AssistantSettings(_env_file=None, data_dir=tmp_path, profile_path=tmp_path / "profile.json", never_list_path=tmp_path / "n.txt")
    a = Assistant(s, store=Store(tmp_path / "a.db"), make_web=lambda: None, llm=PracticeLLM(), notify=lambda *x: None, env_path=tmp_path / ".env")
    api = create_api(a, "secret-token")
    c = TestClient(api, base_url="http://127.0.0.1")
    open_found, checked = [], 0
    for r in api.routes:
        path = getattr(r, "path", "")
        if not path.startswith(("/api", "/media", "/oauth")) or path in OPEN:
            continue
        url = re.sub(r"\{[^}]+\}", "x", path).replace("/publish/x", "/publish/youtube").replace("/connect/x", "/connect/youtube")
        for m in getattr(r, "methods", []) or []:
            checked += 1
            if c.request(m, url, json={}).status_code not in (401, 405):
                open_found.append((m, path))
    assert checked > 80 and open_found == []
    assert TestClient(api, base_url="http://evil.example").get("/api/health").status_code == 403
