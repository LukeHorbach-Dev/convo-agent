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


def inspect(web: WebPlatform, settle_ms: int = 6000) -> str:
    site, me = web.site, web.me
    pages = {
        "profile": (site.profile_path(me), [site.followers_link(me), site.own_profile_marker() or site.nav_profile(),
                                            "header", "main", '[role="main"]']),
        "inbox": (site.inbox_path(), [site.inbox_ready(), site.thread_links(), site.nav_profile(), "main",
                                      '[role="main"]', '[role="navigation"]', '[role="list"]', '[role="listitem"]',
                                      '[role="listbox"]', '[role="option"]', '[role="row"]', '[role="grid"]',
                                      '[role="button"]', 'a[href^="/direct/"]', '[aria-label*="hread"]',
                                      '[aria-label*="hat"]', '[aria-label*="onversation"]']),
    }
    lines = []
    for name, (path, selectors) in pages.items():
        web.sup.goto(site.url(path), expect=".*", ready="body")
        web.page.wait_for_timeout(settle_ms)          # let the app render; nothing is clicked
        info = web.page.evaluate(STRUCTURE_JS)
        lines += [f"== {name}: {info['url']}  (title {info['title']!r}, <main>: {info['main']}, dialogs: {info['dialogs']})",
                  "locators:"] + _counts(web, selectors)
        lines += ["roles:"] + [f"  {n:>4}  {r}" for r, n in info["roles"]]
        lines += ["labels:"] + [f"  {n:>4}  {re.sub(chr(10), ' ', lab)}" for lab, n in info["labels"]]
        lines += ["links:"] + [f"  {n:>4}  {h}" for h, n in info["hrefs"]]
        web.sup.save_screenshot(f"inspect-{name}")
    return "\n".join(lines)
