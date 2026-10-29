"""Convo, the Instagram assistant.

  python -m assistant app            open the app (starts it in the background if it isn't running); Convo.app does this
  python -m assistant run            the app itself, in this window: dashboard + sender
  python -m assistant practice       the practice copy on a pretend Instagram (port 8766)
  python -m assistant import FILE..  load an Instagram download (.zip files or the unzipped folder) without the dashboard
  python -m assistant login          sign in to Instagram in the assistant's browser
  python -m assistant check          try every website step on your account (sends nothing), writes check.txt
  python -m assistant --version
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

from . import __version__
from .config import AssistantSettings


def _serve(a, settings, token: str, port: int, banner: str) -> int:
    import uvicorn
    from .app import create_api
    print(f"{banner}\nDashboard: http://127.0.0.1:{port}/", file=sys.stderr)
    server = uvicorn.Server(uvicorn.Config(create_api(a, token), host="127.0.0.1", port=port, log_level="warning"))
    a.on_quit = lambda: setattr(server, "should_exit", True)
    signal.signal(signal.SIGTERM, lambda *_: setattr(server, "should_exit", True))
    awake = None
    if shutil.which("caffeinate"):         # keep the Mac awake while the app runs (the screen can still sleep)
        awake = subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
    a.start_background()
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        a.shutdown()
        a.store.close()
        if awake:
            awake.terminate()
    return 0


def cmd_run(args) -> int:
    from agent.core.observability import setup_logging
    from .app import Assistant, load_or_create_token
    s = AssistantSettings()
    setup_logging(args.log_level, None)
    a = Assistant(s)
    return _serve(a, s, load_or_create_token(s.data_dir), s.dashboard_port, f"Convo {__version__} for @{s.account_username}")


def cmd_practice(args) -> int:
    from agent.core.config import BrowserConfig
    from agent.core.observability import setup_logging
    from agent.intel.llm import build_provider
    from .app import PRACTICE_PORT, Assistant, load_or_create_token
    from .eyes import Hands
    from .practice import PracticeLLM, StandIn, World, prepare_folder
    from .store import Store
    from agent.platform.browser import BrowserSupervisor
    setup_logging(args.log_level, None)
    real = AssistantSettings()
    base = real.data_dir / "practice"
    prepare_folder(base, real.data_dir)
    s = AssistantSettings(data_dir=base, profile_path=base / "profile.json", never_list_path=base / "never.txt",
                          downloads_dir=base / "downloads", llm=real.llm, eyes=real.eyes,
                          browser=BrowserConfig(user_data_dir=base / "browser"), dashboard_port=PRACTICE_PORT)
    s.pace.message_gap_s = s.pace.unfollow_gap_s = 3
    use_ai = args.real_ai and real.llm.provider == "anthropic" and real.llm.api_key is not None
    s.write_overnight = not use_ai              # the stand-in answers "overnight" at once; real batches take a while
    world = World(s.account_username)
    a = None

    def make_web():
        client = None
        ref = {}
        if use_ai:
            import anthropic
            client = anthropic.Anthropic(api_key=real.llm.api_key.get_secret_value(), timeout=60, max_retries=2)
        else:
            client = StandIn(ref)
        hands = Hands(BrowserSupervisor(s.browser, world.site), world.site, s.account_username, client,
                      model=s.eyes.model, effort=s.eyes.effort, on_cost=lambda usd: a.store.add_spend(usd, "web"),
                      on_view=a._view)
        ref["hands"] = hands
        hands.interrupt = a.sender.halt.is_set
        return hands

    a = Assistant(s, store=Store(s.db_path), make_web=make_web, llm=build_provider(real.llm) if use_ai else PracticeLLM(),
                  practice=world, env_path=base / ".env-practice")
    a.practice_ai = use_ai
    try:
        return _serve(a, s, load_or_create_token(base), PRACTICE_PORT,
                      f"Convo practice ({'real Claude' if use_ai else 'free stand-in'})")
    finally:
        world.stop()


def cmd_app(args) -> int:
    """What Convo.app runs: start the app in the background if needed (or restart an older version), then open its
    window."""
    from .app import load_or_create_token
    from .window import ask_to_quit, health, open_window, wait_until
    s = AssistantSettings()
    port = s.dashboard_port
    token = load_or_create_token(s.data_dir)
    running = health(port)
    if running and running.get("version") != __version__:
        ask_to_quit(port, token)
        wait_until(port, up=False, timeout_s=20)
        running = None
    if not running:
        logs = Path(os.environ.get("CONVO_LOGS", s.data_dir / "logs"))
        logs.mkdir(parents=True, exist_ok=True)
        out = open(logs / "convo.log", "ab")
        subprocess.Popen([sys.executable, "-m", "assistant", "run"], stdout=out, stderr=out, stdin=subprocess.DEVNULL,
                         start_new_session=True, env=os.environ.copy())
        if not wait_until(port, up=True, timeout_s=60):
            print(f"Convo didn't start. Details are in {logs / 'convo.log'}", file=sys.stderr)
            return 1
    how = open_window(f"http://127.0.0.1:{port}/#token={token}", s.data_dir / "window")
    print(f"Convo is open ({how}).", file=sys.stderr)
    return 0


def cmd_import(args) -> int:
    from .brain import never_list, prepare_hellos
    from .export import apply_export, read_export
    from .store import Store
    from agent.intel.profile import Profile
    s = AssistantSettings()
    store = Store(s.db_path)
    summary = apply_export(store, read_export([Path(p) for p in args.files]), s.account_username)
    summary["hellos_ready"] = prepare_hellos(store, Profile.load(s.profile_path), never_list(s.never_list_path))
    print(json.dumps(summary, indent=2))
    return 0


def cmd_login(args) -> int:
    from playwright.sync_api import sync_playwright
    s = AssistantSettings()
    s.browser.user_data_dir.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(str(s.browser.user_data_dir), headless=False)
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto("https://www.instagram.com/accounts/login/")
        input("Sign in (including any code Instagram sends), then come back here and press Enter... ")
        ctx.close()
    print("Signed in. You can open Convo now.")
    return 0


def cmd_check(args) -> int:
    from .app import Assistant
    from .check import run_check
    from .store import Store
    s = AssistantSettings()
    store = Store(s.db_path)
    a = Assistant(s, store=store, notify=lambda *x: None)
    hands = a._instagram()
    try:
        report = run_check(hands, store)
    finally:
        hands.close()
    out = s.data_dir / "diagnostics" / "check.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report + "\n")
    print(report.split("\n== ")[0].rstrip())
    print(f"\nFull report: {out}")
    return 0 if "FAIL" not in report else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="assistant")
    ap.add_argument("--version", action="version", version=f"Convo {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "practice"):
        r = sub.add_parser(name)
        r.add_argument("--log-level", default="INFO")
        if name == "practice":
            r.add_argument("--real-ai", action="store_true", help="use real Claude on the practice Instagram (a few cents)")
    sub.add_parser("app")
    i = sub.add_parser("import")
    i.add_argument("files", nargs="+")
    sub.add_parser("login")
    sub.add_parser("check")
    args = ap.parse_args(argv)
    return {"run": cmd_run, "practice": cmd_practice, "app": cmd_app, "import": cmd_import, "login": cmd_login,
            "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
