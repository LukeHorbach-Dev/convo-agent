"""Instagram assistant.

  python -m assistant run            dashboard + sender (start.sh does this and opens the dashboard)
  python -m assistant import FILE..  load an Instagram download (.zip files or the unzipped folder) without the dashboard
  python -m assistant login          sign in to Instagram in the assistant's browser
  python -m assistant check          try every website step on your account (sends nothing), writes check.txt
  python -m assistant --version
"""
from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

from . import __version__
from .config import AssistantSettings


def cmd_run(args) -> int:
    import uvicorn
    from agent.core.observability import setup_logging
    from .app import Assistant, create_api, load_or_create_token
    s = AssistantSettings()
    setup_logging(args.log_level, None)
    a = Assistant(s)
    a.sender.start()
    token = load_or_create_token(s.data_dir)
    print(f"Instagram assistant {__version__} for @{s.account_username}", file=sys.stderr)
    print(f"Dashboard: http://127.0.0.1:{s.dashboard_port}/", file=sys.stderr)
    server = uvicorn.Server(uvicorn.Config(create_api(a, token), host="127.0.0.1", port=s.dashboard_port, log_level="warning"))
    signal.signal(signal.SIGTERM, lambda *_: setattr(server, "should_exit", True))
    try:
        server.run()
    except KeyboardInterrupt:
        pass
    finally:
        a.stop_job()
        a.sender.shutdown()
        a.store.close()
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
    print("Signed in. Start the app with: bash start.sh")
    return 0


def cmd_check(args) -> int:
    from .app import Assistant
    from .check import run_check
    from .store import Store
    s = AssistantSettings()
    store = Store(s.db_path)
    try:
        hands = Assistant(s, store=store, notify=lambda *a: None)._instagram()
    except Exception as e:
        print(e)
        return 1
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
    ap.add_argument("--version", action="version", version=f"Instagram assistant {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--log-level", default="INFO")
    i = sub.add_parser("import")
    i.add_argument("files", nargs="+")
    sub.add_parser("login")
    sub.add_parser("check")
    args = ap.parse_args(argv)
    return {"run": cmd_run, "import": cmd_import, "login": cmd_login, "check": cmd_check}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
