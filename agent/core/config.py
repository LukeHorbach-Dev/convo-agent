"""Validated configuration. Every policy knob lives here, not scattered as constants.

Values come from environment variables prefixed AGENT_ (nested with __), e.g.
AGENT_PLATFORM=instagram, AGENT_LIMITS__MAX_ATTEMPTS=4. See .env.example.
"""
from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PlatformKind(StrEnum):
    MOCK = "mock"            # in-process synthetic platform (no browser)
    MOCK_WEB = "mock_web"    # synthetic platform driven through a real Chromium
    INSTAGRAM = "instagram"  # real Instagram Web through a persistent Chrome profile


class Mode(StrEnum):
    OBSERVE = "observe"      # read only: ingest events, no drafts
    DRAFT = "draft"          # ingest and draft, never send
    SIMULATE = "simulate"    # full loop; only allowed against mock platforms
    EXECUTE = "execute"      # full loop; real platforms still require approval per message


class BrowserConfig(BaseModel):
    user_data_dir: Path = Path("data/browser-profile")
    headless: bool = False
    # Visible window, but minimised to the Dock: it keeps working while you use other apps.
    start_minimized: bool = False
    executable_path: str | None = None
    navigation_timeout_ms: int = Field(20_000, ge=1_000, le=120_000)
    action_timeout_ms: int = Field(8_000, ge=500, le=60_000)
    health_check_timeout_ms: int = Field(5_000, ge=500, le=60_000)
    max_recoveries_per_run: int = Field(3, ge=0, le=20)


class LLMConfig(BaseModel):
    provider: str = Field("fake", pattern="^(fake|anthropic)$")
    model: str = "claude-opus-5-5"
    api_key: SecretStr | None = None
    timeout_s: float = Field(45.0, gt=0, le=600)
    max_tokens: int = Field(4_000, ge=256, le=64_000)


class LimitsConfig(BaseModel):
    max_attempts: int = Field(4, ge=1, le=20)
    max_retries_per_category: int = Field(3, ge=0, le=20)
    backoff_base_s: float = Field(2.0, gt=0)
    backoff_max_s: float = Field(300.0, gt=0)
    lease_s: float = Field(120.0, gt=1)
    stall_s: float = Field(180.0, gt=1)          # time without observable progress
    max_job_s: float = Field(900.0, gt=1)        # total execution time
    max_state_s: float = Field(600.0, gt=1)      # time in any single non-terminal state
    queue_capacity: int = Field(5_000, ge=1)
    consecutive_account_failures: int = Field(2, ge=1)  # per-account, before skipping
    daily_send_limit: int = Field(25, ge=0)
    max_waiting_intros: int = Field(10, ge=1)   # hellos prepared ahead of your approval; the rest wait their turn
    # Unfollows you ask for are carried out at most this many per 24 hours; the rest wait their turn. Instagram
    # blocks accounts that unfollow too many people at once.
    daily_unfollow_limit: int = Field(50, ge=0)


class CircuitConfig(BaseModel):
    failure_threshold: int = Field(5, ge=1)      # distinct accounts failing the same way
    window_s: float = Field(600.0, gt=0)
    cooldown_s: float = Field(900.0, gt=0)


class DashboardConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = Field(8765, ge=1, le=65535)

    @field_validator("host")
    @classmethod
    def localhost_only(cls, v: str) -> str:
        if v not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("dashboard must bind to localhost")
        return v


class PolicyConfig(BaseModel):
    # Mock platforms may run fully autonomously. Real platforms ignore this flag:
    # approval there is enforced in code (see agent.core.policy).
    mock_requires_approval: bool = False
    intros_enabled: bool = True
    # Whose list the agent watches on your profile. "following": everyone you follow gets one hello (each still
    # needs your approval, within the daily limit). "followers": only people who follow you after the first run.
    audience: Literal["followers", "following"] = "followers"
    # When followers are inferred by diffing a list, the first observation is a baseline, not "new followers".
    # True = also introduce yourself to people who already followed you before the first run.
    intro_existing_followers: bool = False
    replies_enabled: bool = True
    min_age: int = 19
    excluded_usernames: list[str] = Field(default_factory=list)
    # One username per line; merged into excluded_usernames at startup. Never contacted: no intro, no reply.
    never_list_path: Path | None = Path("data/never.txt")
    redact_message_text_in_logs: bool = True
    # An operator approval older than this is not used; the draft goes back for review.
    approval_ttl_hours: float = Field(24.0, gt=0, le=24 * 30)
    retention_days: int = Field(180, ge=1)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", env_nested_delimiter="__", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    db_path: Path = Path("data/agent.db")
    profile_path: Path = Path("data/profile.json")
    platform: PlatformKind = PlatformKind.MOCK
    mode: Mode = Mode.SIMULATE
    account_username: str = "lukehorbach2"
    poll_interval_s: float = Field(30.0, gt=0)
    # How often the followers/following list is re-read (0 = every tick). Messages are still checked every tick.
    list_check_interval_s: float = Field(0.0, ge=0)
    mock_site_url: str = "http://127.0.0.1:8899"
    browser: BrowserConfig = BrowserConfig()
    llm: LLMConfig = LLMConfig()
    limits: LimitsConfig = LimitsConfig()
    circuit: CircuitConfig = CircuitConfig()
    dashboard: DashboardConfig = DashboardConfig()
    policy: PolicyConfig = PolicyConfig()

    def check_consistency(self) -> None:
        if self.mode is Mode.SIMULATE and self.platform is PlatformKind.INSTAGRAM:
            raise ValueError("simulate mode is only allowed against mock platforms")
        if self.platform is PlatformKind.MOCK_WEB:
            # The mock web adapter is treated as "not real" (autonomous sending, no approval). Pointing it at any
            # non-local site would bypass the approval gate, so only loopback http URLs are accepted.
            from urllib.parse import urlsplit
            u = urlsplit(self.mock_site_url)
            if u.scheme != "http" or u.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError(f"mock_site_url must be a local http URL, got {self.mock_site_url!r}")
