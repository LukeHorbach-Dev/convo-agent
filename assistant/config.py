"""Settings, read from .env (AGENT_ prefix, nested with __) so the existing .env and API key keep working."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from agent.core.config import BrowserConfig, LLMConfig


class Pace(BaseModel):
    # Steady gaps so Instagram's pages finish loading and the account isn't hammered. Not randomised.
    message_gap_s: float = Field(30.0, ge=0)
    unfollow_gap_s: float = Field(40.0, ge=0)
    # Daily caps are Luke's settings; they stay numbers because Instagram blocks accounts that send or unfollow
    # too much in a day.
    daily_messages: int = Field(50, ge=0, le=300)
    daily_unfollows: int = Field(60, ge=0, le=300)


class Eyes(BaseModel):
    # The model that looks at Instagram's page and clicks. Haiku keeps it to about a cent per message or unfollow;
    # Sonnet is steadier on odd pages and costs roughly twenty times as much.
    model: str = Field("claude-haiku-5-5", pattern=r"^claude-(haiku|sonnet|opus)-[0-9-]+$")
    effort: str = Field("medium", pattern="^(low|medium|high)$")


class AssistantSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENT_", env_nested_delimiter="__", env_file=".env", extra="ignore")

    account_username: str = "lukehorbach2"
    data_dir: Path = Path("data")
    profile_path: Path = Path("data/profile.json")
    never_list_path: Path = Path("data/never.txt")
    downloads_dir: Path = Path.home() / "Downloads"
    llm: LLMConfig = LLMConfig()
    browser: BrowserConfig = BrowserConfig()
    pace: Pace = Pace()
    eyes: Eyes = Eyes()
    daily_ai_budget_usd: float = Field(5.0, ge=0.5, le=100)   # sending and writing pause for the day past this
    reply_window_days: int = Field(90, ge=1)     # only draft replies for chats where they wrote in this window
    min_age: int = 19
    dashboard_port: int = 8765

    @property
    def db_path(self) -> Path:
        return self.data_dir / "assistant.db"
