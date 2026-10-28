"""Dependency wiring. Everything external (platform, LLM, database) is injected so tests can replace it."""
from __future__ import annotations

from dataclasses import dataclass

from .core.config import PlatformKind, Settings
from .core.db import Database
from .engine.engine import Engine
from .intel.llm import LLMProvider, build_provider
from .intel.profile import Profile
from .intel.reasoning import Drafter
from .platform.base import PlatformAdapter
from .platform.mock import MockPlatform, MockWorld


@dataclass
class App:
    settings: Settings
    db: Database
    platform: PlatformAdapter
    llm: LLMProvider
    profile: Profile
    engine: Engine
    world: MockWorld | None = None


def seed_demo_world(world: MockWorld) -> None:
    """Synthetic accounts used by the demo and end-to-end tests."""
    world.new_follower("mia.skis", "Mia", bio="Snowboarder")
    world.new_follower("ollie_paints", "Ollie", bio="Painter and coffee nerd")
    world.new_follower("kai_2010", "Kai")                      # birth year in name: never contacted
    world.new_follower("restricted.rae", "Rae")                # messaging unavailable
    world.faults.no_message_button.add("restricted.rae")


def build_platform(settings: Settings, world: MockWorld | None = None) -> tuple[PlatformAdapter, MockWorld | None]:
    if settings.platform is PlatformKind.MOCK:
        world = world or MockWorld(settings.account_username)
        return MockPlatform(world), world
    from .platform.browser import BrowserSupervisor
    from .platform.web import WebPlatform
    from .platform.sites import INSTAGRAM, mock_site_profile
    if settings.platform is PlatformKind.INSTAGRAM:
        site = INSTAGRAM
    else:
        site = mock_site_profile(settings.mock_site_url)
    sup = BrowserSupervisor(settings.browser, site)
    audience = settings.policy.audience
    # Following lists run to well over a thousand accounts, so that list is scrolled much further.
    return WebPlatform(sup, site, settings.account_username, audience=audience,
                       max_scrolls=200 if audience == "following" else 8), world


def build_app(settings: Settings | None = None, *, platform: PlatformAdapter | None = None, llm: LLMProvider | None = None,
              db: Database | None = None, world: MockWorld | None = None, profile: Profile | None = None) -> App:
    settings = settings or Settings()
    settings.check_consistency()
    from .core.policy import load_never_list
    load_never_list(settings)
    db = db or Database(settings.db_path)
    db.migrate()
    if platform is None:
        platform, world = build_platform(settings, world)
    llm = llm or build_provider(settings.llm)
    profile = profile or Profile.load(settings.profile_path)
    manual = platform.is_real and getattr(llm, "name", "") == "fake"
    engine = Engine(settings, db, platform, Drafter(profile, llm, manual_replies=manual), profile)
    return App(settings, db, platform, llm, profile, engine, world)
