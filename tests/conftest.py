from __future__ import annotations

from pathlib import Path

import pytest

from agent.app import build_app
from agent.core.config import Settings
from agent.core.db import Database
from agent.intel.llm import FakeLLM
from agent.platform.mock import MockPlatform, MockWorld

ROOT = Path(__file__).resolve().parents[1]


class Crash(BaseException):
    """Simulates the process dying: not an Exception, so no handler in the app catches it."""


def make_settings(**kw) -> Settings:
    s = Settings(_env_file=None, db_path=":memory:", profile_path=ROOT / "tests/fixtures/profile.json", **kw)
    s.policy.never_list_path = None          # tests never read the operator's personal never-list
    s.limits.backoff_base_s = 0.001
    s.limits.backoff_max_s = 0.002
    return s


@pytest.fixture
def settings():
    return make_settings()


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "agent.db")
    d.migrate()
    yield d
    d.close()


@pytest.fixture
def world():
    return MockWorld("lukehorbach2")


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def app(settings, db, world, llm):
    return build_app(settings, db=db, platform=MockPlatform(world), world=world, llm=llm)


def settle(engine, ticks: int = 6) -> None:
    """Run ticks, letting tiny test backoffs elapse between them."""
    import time
    for _ in range(ticks):
        engine.run_once()
        time.sleep(0.01)


def job_states(db, kind=None) -> dict[str, str]:
    sql = "SELECT subject_key, state FROM jobs" + (" WHERE kind = ?" if kind else "")
    return {r["subject_key"]: r["state"] for r in db.all(sql, (kind,) if kind else ())}
