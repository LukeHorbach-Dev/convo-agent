"""Command line entry point.

  python -m agent demo                      deterministic end-to-end run on the mock platform, prints a report
  python -m agent run                       scheduler + dashboard (platform/mode from config or flags)
  python -m agent run --platform mock_web   same, through a real Chromium against the local mock site
  python -m agent login                     open the agent's Chrome profile so you can sign in by hand
  python -m agent migrate                   apply database migrations
  python -m agent diagnose                  print a sanitized diagnostic bundle
  python -m agent import-list FILE          queue introductions for usernames in FILE (still policy-checked and approved)
  python -m agent inspect                   report the structure of your profile and inbox pages (for fixing locators)
"""
from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from pathlib import Path

from .core.config import Mode, PlatformKind, Settings
from .core.observability import setup_logging

log = logging.getLogger("main")


def _settings(args) -> Settings:
    overrides = {}
    if getattr(args, "platform", None):
        overrides["platform"] = PlatformKind(args.platform)
    if getattr(args, "mode", None):
        overrides["mode"] = Mode(args.mode)
    if getattr(args, "db", None):
        overrides["db_path"] = args.db
    if getattr(args, "headless", False):
        overrides["browser"] = Settings().browser.model_copy(update={"headless": True})
    s = Settings(**overrides)
    if s.platform is PlatformKind.INSTAGRAM and s.mode is Mode.SIMULATE:
        s.mode = Mode.EXECUTE  # the real platform runs in execute mode, where every send needs approval
    try:
        s.check_consistency()          # validate before starting anything (mock site, browser, scheduler)
    except ValueError as e:
        raise SystemExit(f"configuration error: {e}")
    return s


def cmd_demo(args) -> int:
    from .core.db import Database
    from .demo import run_demo
    report = run_demo(Database(args.db or ":memory:"))
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["invariants_ok"] else 1


def cmd_run(args) -> int:
    import uvicorn
    from .app import build_app, seed_demo_world
    from .engine.scheduler import InstanceLock, Scheduler
    from .web.dashboard import create_dashboard, load_or_create_token
    s = _settings(args)
    setup_logging(args.log_level, str(s.data_dir / "agent.log") if args.log_file else None)
    lock = InstanceLock(s.data_dir / "scheduler.lock")
    lock.acquire()
    site_server = None
    world = None
    if s.platform in (PlatformKind.MOCK, PlatformKind.MOCK_WEB):
        from .platform.mock import MockWorld
        world = MockWorld(s.account_username)
        seed_demo_world(world)
        if s.platform is PlatformKind.MOCK_WEB:
            from .platform.mock_site import SiteServer
            from urllib.parse import urlsplit
            port = urlsplit(s.mock_site_url).port or 80
            site_server = SiteServer(world, port).start()
    app = build_app(s, world=world)
    sched = Scheduler(app.engine, s.poll_interval_s)
    token = load_or_create_token(s.data_dir)
    sched.start()
    print(f"Dashboard: http://{s.dashboard.host}:{s.dashboard.port}/  (token in {s.data_dir / 'dashboard-token'})", file=sys.stderr)
    server = uvicorn.Server(uvicorn.Config(create_dashboard(app, token, sched), host=s.dashboard.host, port=s.dashboard.port, log_level="warning"))

    def shutdown(*_):
        server.should_exit = True
    signal.signal(signal.SIGTERM, shutdown)
    try:
        server.run()
    except KeyboardInterrupt:
        pass                       # Ctrl+C: shut down quietly; cleanup below always runs
    finally:
        clean = sched.stop(timeout_s=30)
        app.platform.close()
        if site_server:
            site_server.stop()
        app.db.close()
        lock.release()
        log.info("shutdown", extra={"outcome": "clean" if clean else "worker did not stop in time"})
    return 0 if clean else 2


def cmd_login(args) -> int:
    from playwright.sync_api import sync_playwright
    s = _settings(args)
    s.browser.user_data_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(s.browser.user_data_dir), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.instagram.com/accounts/login/")
        input("Sign in (including any verification) in the browser window, then press Enter here to save the session... ")
        ctx.close()
    print("Session saved in", s.browser.user_data_dir)
    return 0


def cmd_import_list(args) -> int:
    from .app import build_app
    s = _settings(args)
    app = build_app(s)
    names = Path(args.file).read_text().splitlines()
    print(json.dumps(app.engine.import_intro_list(names), indent=2))
    return 0


def cmd_inspect(args) -> int:
    from .app import build_platform
    from .platform.inspect_page import inspect
    s = _settings(args)
    if s.platform is PlatformKind.MOCK:
        raise SystemExit("inspect needs a browser platform: add --platform instagram (or mock_web)")
    web, _ = build_platform(s)
    try:
        report = inspect(web)
    finally:
        web.close()
    out = s.data_dir / "diagnostics" / "inspect.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n")
    print(report)
    print(f"\nSaved to {out} (screenshots next to it)")
    return 0


def cmd_migrate(args) -> int:
    from .core.db import Database
    s = _settings(args)
    print("schema version", Database(s.db_path).migrate())
    return 0


def cmd_diagnose(args) -> int:
    from .app import build_app
    from .web.dashboard import create_dashboard
    from fastapi.testclient import TestClient
    s = _settings(args)
    app = build_app(s)
    client = TestClient(create_dashboard(app, "diag"), base_url="http://127.0.0.1")
    print(json.dumps(client.get("/api/diagnostics", headers={"X-Agent-Token": "diag"}).json(), indent=2, default=str))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("demo", "run", "login", "migrate", "diagnose", "import-list", "inspect"):
        p = sub.add_parser(name)
        if name == "import-list":
            p.add_argument("file", help="text file, one username per line")
        p.add_argument("--db")
        p.add_argument("--platform", choices=[k.value for k in PlatformKind])
        p.add_argument("--mode", choices=[m.value for m in Mode])
        p.add_argument("--headless", action="store_true")
        p.add_argument("--log-level", default="INFO")
        p.add_argument("--log-file", action="store_true")
    args = ap.parse_args(argv)
    return {"demo": cmd_demo, "run": cmd_run, "login": cmd_login, "migrate": cmd_migrate, "diagnose": cmd_diagnose,
            "import-list": cmd_import_list, "inspect": cmd_inspect}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
