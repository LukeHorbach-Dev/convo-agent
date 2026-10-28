"""Page structure report for fixing locators (`python -m agent inspect`).

Opens your profile and your inbox in the agent's browser, only looks (no clicks), and reports which locators match,
how many of them are visible, and the page's roles, labels and link shapes. Message text is never included.
"""
from __future__ import annotations

import re

from .web import WebPlatform

STRUCTURE_JS = """() => {
  const vis = el => (el.checkVisibility ? el.checkVisibility() : el.offsetParent !== null);
  const all = [...document.querySelectorAll('*')].filter(vis);
  const tally = xs => { const m = {}; xs.forEach(x => m[x] = (m[x] || 0) + 1);
                        return Object.entries(m).sort((a, b) => b[1] - a[1]).slice(0, 25); };
  const roles = tally(all.filter(e => e.getAttribute('role')).map(e => e.tagName.toLowerCase() + '[role=' + e.getAttribute('role') + ']'));
  const labels = tally(all.filter(e => e.getAttribute('aria-label')).map(e => e.tagName.toLowerCase() + ' "' + e.getAttribute('aria-label').slice(0, 40) + '"'));
  const hrefs = tally([...document.querySelectorAll('a[href]')].filter(vis).map(a => {
    const p = new URL(a.href, location.href).pathname.split('/').filter(Boolean);
    if (!p.length) return '/';
    if (p[0] === 'direct' && p[1] === 't') return '/direct/t/<id>';
    const known = ['direct', 'accounts', 'explore', 'reels', 'p', 'reel', 'stories'].includes(p[0]);
    return '/' + (known ? p[0] : '<user>') + (p.length > 1 ? '/' + p.slice(1).join('/').replace(/[A-Za-z0-9_-]{20,}/g, '<id>') : '');
  }));
  return {url: location.pathname, title: document.title, roles, labels, hrefs,
          main: !!document.querySelector('main'), dialogs: document.querySelectorAll('[role=dialog]').length};
}"""


def _counts(web: WebPlatform, selectors: list[str]) -> list[str]:
    out = []
    for sel in selectors:
        try:
            loc = web.page.locator(sel)
            out.append(f"  {loc.filter(visible=True).count():>4} visible / {loc.count():>4} total   {sel}")
        except Exception as e:
            out.append(f"  error {str(e).splitlines()[0][:60]}   {sel}")
    return out


def _report(web: WebPlatform, name: str, selectors: list[str], settle_ms: int) -> list[str]:
    web.page.wait_for_timeout(settle_ms)              # let the app render
    info = web.page.evaluate(STRUCTURE_JS)
    lines = [f"== {name}: {re.sub(r'/direct/t/[^/]+', '/direct/t/<id>', info['url'])}  (title {info['title']!r}, "
             f"<main>: {info['main']}, dialogs: {info['dialogs']})", "locators:"] + _counts(web, selectors)
    lines += ["roles:"] + [f"  {n:>4}  {r}" for r, n in info["roles"]]
    lines += ["labels:"] + [f"  {n:>4}  {re.sub(chr(10), ' ', lab)}" for lab, n in info["labels"]]
    lines += ["links:"] + [f"  {n:>4}  {h}" for h, n in info["hrefs"]]
    web.sup.save_screenshot(f"inspect-{name}")
    return lines


def inspect(web: WebPlatform, settle_ms: int = 6000) -> str:
    """Profile and inbox: look only. Then open the newest conversation (this marks it as seen, like opening it
    yourself) and that person's profile, to check the thread and Message-button locators. Sends nothing."""
    site, me = web.site, web.me
    lines: list[str] = []
    web.sup.goto(site.url(site.profile_path(me)), expect=".*", ready="body")
    lines += _report(web, "profile", [site.list_link(me, "followers"), site.list_link(me, "following"), site.own_profile_marker() or site.nav_profile(),
                                      "header", "main", '[role="main"]'], settle_ms)
    web.sup.goto(site.url(site.inbox_path()), expect=".*", ready="body")
    lines += _report(web, "inbox", [site.inbox_ready(), site.thread_rows(), site.nav_profile(), "main",
                                    '[role="main"]', '[role="navigation"]', '[aria-label="Thread list"] [role="button"]',
                                    '[role="button"]:has(abbr)', '[role="listitem"]', '[role="row"]', '[role="grid"]',
                                    'a[href^="/direct/"]'], settle_ms)
    rows = web.page.locator(site.thread_rows()).filter(visible=True)
    if rows.count() == 0:
        return "\n".join(lines + ["== thread: skipped, no conversation rows matched"])
    rows.first.click()
    try:
        web.page.wait_for_url(re.compile(r"/direct/t/[^/]+"), timeout=web.sup.cfg.navigation_timeout_ms)
    except Exception:
        lines.append("note: clicking the first row did not open a /direct/t/<id>/ address")
    lines += _report(web, "thread", [site.thread_ready(), site.thread_username(), site.composer(), site.send_button(),
                                     '[role="main"]', '[role="main"] header', '[role="textbox"]', '[contenteditable="true"]',
                                     '[role="grid"]', '[role="row"]', '[role="gridcell"]', 'div[dir="auto"]',
                                     '[aria-label="Message"]', '[aria-label*="onversation"]'], settle_ms)
    try:
        msgs = web.page.evaluate(site.read_messages_js())
        lines.append("message reader: " + ("found no thread area" if msgs is None else
                     f"{sum(m['direction'] == 'in' for m in msgs)} incoming, {sum(m['direction'] == 'out' for m in msgs)} outgoing"))
    except Exception as e:
        lines.append(f"message reader: error {str(e).splitlines()[0][:80]}")
    from .web import username_from_href
    names = {username_from_href(a.get_attribute("href")) for a in web.page.locator(site.thread_username()).filter(visible=True).all()}
    names -= {None, me}
    lines.append(f"conversation partner candidates: {len(names)}")
    if len(names) == 1:
        web.sup.goto(site.url(site.profile_path(names.pop())), expect=".*", ready="body")
        lines += _report(web, "their-profile", [site.message_button(), *site.restricted_markers(), "header",
                                                'header [role="button"]', "header button", '[role="button"]:text-is("Message")',
                                                ':text-is("Message")'], settle_ms)
        texts = [t.strip()[:20] for t in web.page.locator('header [role="button"], header button').filter(visible=True).all_inner_texts()]
        lines.append(f"header button texts: {[t for t in texts if t]}")
    return "\n".join(lines)
