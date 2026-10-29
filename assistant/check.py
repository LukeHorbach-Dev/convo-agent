"""Self-check for the assistant (`python -m assistant check`): tries every website step the assistant uses, on the
real account, without sending anything or unfollowing anyone. Writes data/diagnostics/check.txt."""
from __future__ import annotations

import re
import time

from agent.platform.inspect_page import _report
from agent.platform.selfcheck import SHORT_TEXTS_JS

from .store import Store


def run_check(web, store: Store | None, settle_ms: int = 3000) -> str:
    site, me = web.site, web.me
    results, details, ctx = [], [], {}

    def step(name, fn, structure=None):
        t = time.monotonic()
        try:
            results.append((name, True, f"{fn() or ''} ({time.monotonic() - t:.0f}s)"))
        except Exception as e:
            results.append((name, False, f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"))
            web.sup.save_screenshot("check-" + re.sub(r"\W+", "-", name.lower()).strip("-"))
            try:
                details.extend(_report(web, f"FAILED {name}", structure or [], settle_ms))
                details.append("short texts in <main>: " + ", ".join(repr(x) for x in web.page.evaluate(SHORT_TEXTS_JS)))
            except Exception:
                pass

    def signed_in():
        assert web.whoami() == me
        return f"signed in as {me}"
    step("1 signed in", signed_in, [site.own_profile_marker() or site.nav_profile()])

    def inbox():
        web._open_inbox()
        web.page.wait_for_timeout(settle_ms)
        ctx["rows"] = web._visible(site.thread_rows()).count()
        return f"{ctx['rows']} chats visible"
    step("2 messages page", inbox, [site.inbox_ready(), site.thread_rows()])

    def open_row():
        if not ctx.get("rows"):
            return "skipped: no chats"
        web._visible(site.thread_rows()).first.click()
        web.page.wait_for_url(re.compile(r"/direct/t/[^/]+"), timeout=web.sup.cfg.navigation_timeout_ms)
        key = re.search(r"/direct/t/([^/]+)", web.page.url).group(1)
        snap = web._read_open_thread(key)
        assert snap.messages, "this chat has messages but none were read"
        ctx["partner"] = snap.username
        ins = sum(m.direction == "in" for m in snap.messages)
        return f"@{snap.username}: {ins} from them, {len(snap.messages) - ins} from you"
    step("3 open a chat from the inbox and read it", open_row, [site.thread_ready(), site.thread_username(), site.composer()])

    # Someone you follow, from the download if it's loaded; otherwise the person from step 3.
    who = None
    if store is not None:
        row = store.one("SELECT p.username FROM people p JOIN threads t ON t.username = p.username WHERE p.i_follow = 1 "
                        "ORDER BY t.last_at DESC LIMIT 1") or store.one("SELECT username FROM people WHERE i_follow = 1 LIMIT 1")
        who = row["username"] if row else None
    who = who or ctx.get("partner")

    def open_by_username():
        if not who:
            raise RuntimeError("no one to try: load a download first")
        snap = web.open_dm(who)
        return f"@{who}: chat opened ({len(snap.messages)} messages read)"
    step("4 open a chat from a username", open_by_username,
         [site.message_button(), site.dm_expand(), site.composer(), '[aria-label*="xpand" i]'])

    def message_box():
        web._visible(site.composer()).first.wait_for(state="visible", timeout=web.sup.cfg.action_timeout_ms)
        return "message box found; nothing typed"
    step("5 message box", message_box, [site.composer()])

    def unfollow_menu():
        if not who:
            raise RuntimeError("no one to try")
        web.sup.goto(site.url(site.profile_path(who)), expect=re.escape(f"/{who}"), ready="main")
        btn = web._visible(site.following_button())
        btn.first.wait_for(state="visible", timeout=site.ready_timeout_ms or web.sup.cfg.action_timeout_ms)
        btn.first.click()
        web._visible(site.unfollow_confirm()).first.wait_for(state="visible", timeout=web.sup.cfg.action_timeout_ms)
        web.page.keyboard.press("Escape")
        web.page.wait_for_timeout(800)
        cancel = web.page.locator('[role="dialog"] :text-is("Cancel")').filter(visible=True)
        if cancel.count():
            cancel.first.click()
        web._visible(site.following_button()).first.wait_for(state="visible", timeout=web.sup.cfg.action_timeout_ms)
        return f"@{who}: the Unfollow option is there; closed without pressing it"
    step("6 unfollow menu (not pressed)", unfollow_menu, [site.following_button(), site.follow_button(), site.unfollow_confirm()])

    ok = sum(1 for _, p, _ in results if p)
    lines = [f"Self-check: {ok} of {len(results)} steps passed. Nothing was sent and nobody was unfollowed.", ""]
    lines += [f"{'PASS' if p else 'FAIL'}  {n}: {note}" for n, p, note in results]
    return "\n".join(lines + ([""] + details if details else []))
