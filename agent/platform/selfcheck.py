"""Self-check (`python -m agent check`): tries every step the agent takes on the live site, without sending a message
or unfollowing anyone, and reports PASS/FAIL per step with a page-structure report and screenshot for each failure.

One run gives everything needed to fix a broken step, instead of one problem per round trip.
"""
from __future__ import annotations

import re
import time
import traceback

from .inspect_page import _report
from .web import WebPlatform, username_from_href


def self_check(web: WebPlatform, settle_ms: int = 3000) -> str:
    site, me = web.site, web.me
    results: list[tuple[str, bool, str]] = []
    details: list[str] = []
    ctx: dict = {}

    def step(name: str, fn, structure: list[str] | None = None) -> None:
        started = time.monotonic()
        try:
            note = fn() or ""
            results.append((name, True, f"{note} ({time.monotonic() - started:.0f}s)"))
        except Exception as e:
            results.append((name, False, f"{type(e).__name__}: {str(e).splitlines()[0][:200]}"))
            web.sup.save_screenshot("check-" + re.sub(r"\W+", "-", name.lower()).strip("-"))
            try:
                details.extend(_report(web, f"FAILED {name}", structure or [], settle_ms))
            except Exception:
                details.append(f"== FAILED {name}: no page report ({traceback.format_exc(limit=1).splitlines()[-1]})")

    def signed_in():
        assert web.whoami() == me
        return f"signed in as {me}"
    step("1 signed in", signed_in, [site.own_profile_marker() or site.nav_profile(), "header"])

    def following_list():
        reader = WebPlatform(web.sup, site, me, max_scrolls=3, audience="following")
        reader.batch_wait_s = 4
        found = reader.poll_followers()
        assert found, "list opened but no accounts read"
        ctx["someone"] = found[0].username
        total = web.page.locator(site.list_link(me, "following")).first.inner_text() if web.page.locator(site.list_link(me, "following")).count() else "?"
        return f"read {len(found)} in 3 scrolls; profile says {total.strip()!r}; first: @{found[0].username}"
    step("2 following list", following_list, [site.list_link(me, "following"), site.followers_ready(), site.follower_links()])

    def inbox():
        web.sup.goto(site.url(site.inbox_path()), expect=r"/direct/", ready=site.inbox_ready())
        web.page.wait_for_timeout(settle_ms)
        rows = web.page.locator(site.thread_rows()).filter(visible=True)
        ctx["rows"] = rows.count()
        return f"{ctx['rows']} conversations visible"
    step("3 messages page", inbox, [site.inbox_ready(), site.thread_rows(), '[aria-label="Thread list"] [role="button"]'])

    def conversation():
        if not ctx.get("rows"):
            return "skipped: no conversations"
        web.page.locator(site.thread_rows()).filter(visible=True).first.click()
        web.page.wait_for_url(re.compile(r"/direct/t/[^/]+"), timeout=web.sup.cfg.navigation_timeout_ms)
        key = re.search(r"/direct/t/([^/]+)", web.page.url).group(1)
        snap = web._read_open_thread(key)
        web._visible(site.composer()).first.wait_for(state="visible", timeout=web.sup.cfg.action_timeout_ms)
        ins = sum(m.direction == "in" for m in snap.messages)
        # A chat from the inbox has messages; reading none would mean hellos could go into existing chats.
        assert snap.messages, "this chat has messages but the app read none"
        return f"with @{snap.username}: {ins} from them, {len(snap.messages) - ins} from you; message box found"
    step("4 open a conversation", conversation, [site.thread_ready(), site.thread_username(), site.composer()])

    def open_dm():
        who = ctx.get("someone")
        if not who:
            raise RuntimeError("no account from step 2 to try")
        snap = web.open_dm(who)
        web._visible(site.composer()).first.wait_for(state="visible", timeout=web.sup.cfg.action_timeout_ms)
        return f"@{who}: conversation opened ({len(snap.messages)} messages); nothing typed"
    step("5 Message button opens a conversation", open_dm,
         [site.message_button(), site.dm_expand(), site.composer(), '[aria-label*="xpand" i]', '[contenteditable="true"]'])

    def send_button_rule():
        # Instagram shows Send only after typing; the agent falls back to Enter. Nothing is typed here.
        n = web._visible(site.send_button()).count()
        return "Send button visible" if n else "Send button hidden until text is typed (expected); Enter is used then"
    step("6 send control", send_button_rule)

    def unfollow_menu():
        who = ctx.get("someone")
        if not who:
            raise RuntimeError("no account from step 2 to try")
        web.sup.goto(site.url(site.profile_path(who)), expect=re.escape(f"/{who}"), ready="body")
        btn = web._visible(site.following_button())
        btn.first.wait_for(state="visible", timeout=site.ready_timeout_ms or web.sup.cfg.action_timeout_ms)
        btn.first.click()
        web._visible(site.unfollow_confirm()).first.wait_for(state="visible", timeout=web.sup.cfg.action_timeout_ms)
        web.page.keyboard.press("Escape")       # close the menu; Unfollow is never pressed
        web.page.wait_for_timeout(800)
        cancel = web.page.locator('[role="dialog"] :text-is("Cancel")').filter(visible=True)
        if cancel.count():
            cancel.first.click()
        web._visible(site.following_button()).first.wait_for(state="visible", timeout=web.sup.cfg.action_timeout_ms)
        return f"@{who}: Following → menu shows Unfollow; closed without unfollowing"
    step("7 unfollow menu (not pressed)", unfollow_menu,
         [site.following_button(), site.follow_button(), site.unfollow_confirm(), '[role="dialog"]', '[role="menuitem"]'])

    ok = sum(1 for _, passed, _ in results if passed)
    lines = [f"Self-check: {ok} of {len(results)} steps passed. Nothing was sent and nobody was unfollowed.", ""]
    lines += [f"{'PASS' if passed else 'FAIL'}  {name}: {note}" for name, passed, note in results]
    return "\n".join(lines + ([""] + details if details else []))
