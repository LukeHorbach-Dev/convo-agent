"""Works through what Luke approved, one at a time, at a steady pace: open the chat, check it's the right one and
nothing changed, send, confirm. It also works through the unfollow list. The website itself is handled by
eyes.Hands (Claude looks at the page and clicks; the app types only the approved text). When something goes wrong
it decides, and says in plain words, whether to try again later, skip that person, or stop and wait for Luke.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from difflib import SequenceMatcher

from agent.core.errors import (AmbiguousState, AuthenticationRequired, BrowserCrashed, DeliveryUnknown, InterventionRequired,
                               OperatorStop, PermissionRestricted, PlatformUnavailable, RateLimited, SelectorDrift,
                               TransientError)
from agent.notify import mac_notify

from .store import Store, now_iso

log = logging.getLogger("sender")

# Text Instagram shows when it has limited the account. Seeing any of it means: stop for a long while.
BLOCKED = re.compile(r"try again later|we restrict certain activity|we limit how often|action blocked|"
                     r"temporarily blocked|unusual activity|you can't (send|use) .* right now", re.I)


def _letters(s: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", (s or "").lower())).strip()


def similar(a: str, b: str) -> bool:
    """Same message, allowing for how Claude copies it off the screen (emoji, punctuation, a slip of a letter)."""
    la, lb = _letters(a), _letters(b)
    if not la or not lb:
        return re.sub(r"\s+", "", a or "") == re.sub(r"\s+", "", b or "") or (not la and not lb and bool(a) and bool(b))
    if la == lb or SequenceMatcher(None, la, lb).ratio() >= 0.8:
        return True
    return min(len(la), len(lb)) >= 20 and (la in lb or lb in la)    # one shown cut short


def classify(e: Exception) -> tuple[str, str, float]:
    """What to do about a problem: (action, plain words, wait seconds).
    action: stop (needs Luke), cooldown (Instagram wants a break), later (temporary; try again soon),
            skip (this person only), page (the page looked different; try again once, then skip)."""
    if isinstance(e, AuthenticationRequired):
        return "stop", "Instagram wants you to sign in again. Stop the app, run the sign-in step, then start it again.", 0
    if isinstance(e, (OperatorStop, InterventionRequired)):
        return "stop", str(e), 0
    if isinstance(e, RateLimited):
        return "cooldown", "Instagram asked to slow down. Taking a 6 hour break, then carrying on by itself.", 6 * 3600
    if isinstance(e, PermissionRestricted):
        return "skip", str(e).split(": ", 1)[-1], 0
    if isinstance(e, (SelectorDrift, AmbiguousState)):
        return "page", str(e).split(": ", 1)[-1], 0
    if isinstance(e, (PlatformUnavailable, BrowserCrashed)):
        return "later", "Instagram didn't load properly. Trying again in 10 minutes.", 600
    if isinstance(e, TransientError):
        return "later", f"Couldn't carry on ({e}). Trying again in 10 minutes.", 600
    return "page", f"something unexpected happened ({type(e).__name__})", 0


class Sender:
    """Runs on its own thread, which owns the browser (the browser library needs that)."""

    def __init__(self, store: Store, settings, make_web: Callable[[], object], notify=mac_notify,
                 sleep: Callable[[float], None] | None = None):
        self.store, self.s, self.make_web, self.notify = store, settings, make_web, notify
        self.web = None
        self.active = False                     # Luke pressed Send (sending and unfollowing run)
        self.status = "Waiting for you"
        self.wait_until = 0.0
        self.page_failures = 0                  # consecutive "page looked different" problems, across people
        self._stop = threading.Event()
        self._sleep = sleep or (lambda s: self._stop.wait(s))
        self._thread: threading.Thread | None = None
        self.identity_ok = False
        self._reset = False
        self.just_one = False                   # "Send just one first": pause after the first message goes
        self._budget_logged = False

    # ------------------------------------------------------------------ control
    def start(self) -> "Sender":
        self._thread = threading.Thread(target=self._loop, name="sender", daemon=True)
        self._thread.start()
        return self

    def go(self, just_one: bool = False) -> None:
        self.active, self.wait_until, self.page_failures = True, 0.0, 0
        self.just_one = just_one
        self.status = "Starting"

    def pause(self, why: str = "Paused") -> None:
        self.active = False
        self.status = why

    def reset_browser(self) -> None:
        """Open the browser afresh before the next send (e.g. after a new AI key); done on the sender's own thread."""
        self._reset = True

    def shutdown(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(15)

    # ------------------------------------------------------------------ loop
    def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                if not self.active or time.time() < self.wait_until:
                    self._stop.wait(2)
                    continue
                try:
                    did = self.step()
                except Exception as e:                       # never let the thread die
                    log.exception("sender step failed")
                    self.store.log("problem", f"Unexpected problem: {e}")
                    did = False
                if not did:
                    self._stop.wait(5)
        finally:
            if self.web is not None:
                try:
                    self.web.close()
                except Exception:
                    pass

    def step(self) -> bool:
        """Do one thing. Returns True if it did something."""
        pace = self.s.pace
        msg = self.store.one("SELECT * FROM drafts WHERE status = 'approved' ORDER BY kind = 'hello', tries, id LIMIT 1")
        unf = self.store.one("SELECT * FROM unfollows WHERE status = 'queued' ORDER BY tries, queued_at, username LIMIT 1")
        if msg and self.store.sent_today() >= pace.daily_messages:
            msg = None
            self.status = f"Sent today's {pace.daily_messages}. The rest go tomorrow by themselves."
        if unf and self.store.unfollowed_today() >= pace.daily_unfollows:
            unf = None
        budget = getattr(self.s, "daily_ai_budget_usd", 0) or 0
        if (msg or unf) and budget and self.store.spent_today() >= budget:
            self.status = (f"Reached today's AI spending limit (${budget:.2f}). Carries on tomorrow by itself, "
                           "or raise the limit in Settings.")
            if not self._budget_logged:
                self._budget_logged = True
                self.store.log("warn", self.status)
                self.notify("Instagram assistant paused", self.status)
            self.wait_until = time.time() + 1800
            return False
        self._budget_logged = False
        if not msg and not unf:
            if self.store.scalar("SELECT COUNT(*) FROM drafts WHERE status = 'approved'") == 0 and \
               self.store.scalar("SELECT COUNT(*) FROM unfollows WHERE status = 'queued'") == 0:
                if self.status.startswith(("Sending", "Unfollowing", "Starting")):
                    self.store.log("good", "All done for now.")
                    self.notify("Instagram assistant", "All done: everything you approved has gone out.")
                self.status = "All done for now"
                self.active = False
            return False
        if not self._ready():
            return False
        if msg:
            self.status = f"Sending to {('@' + msg['username']) if msg['username'] else msg['other_name']}"
            self.send_one(msg)
            self._sleep(pace.message_gap_s)
        else:
            self.status = f"Unfollowing @{unf['username']}"
            self.unfollow_one(unf)
            self._sleep(pace.unfollow_gap_s)
        return True

    def _ready(self) -> bool:
        """Open the browser and check it's signed in as Luke, once per session."""
        try:
            if self._reset:
                self._reset = False
                if self.web is not None:
                    try:
                        self.web.close()
                    except Exception:
                        pass
                self.web, self.identity_ok = None, False
            if self.web is None:
                self.web = self.make_web()
            if not self.identity_ok:
                me = self.web.whoami()
                if me.lower() != self.s.account_username.lower():
                    raise AuthenticationRequired(f"signed in as {me}, not {self.s.account_username}")
                self.identity_ok = True
            return True
        except Exception as e:
            self._handle(e, who=None)
            return False

    # ------------------------------------------------------------------ messages
    def send_one(self, d: dict) -> None:
        with self.store.transaction():
            self.store.execute("UPDATE drafts SET status = 'sending', tries = tries + 1, updated_at = ? WHERE id = ? AND status = 'approved'",
                               (now_iso(), d["id"]))
        who = ("@" + d["username"]) if d["username"] else d["other_name"]
        spent_before = getattr(self.web, "total_usd", 0.0)
        try:
            chat = self._open_chat(d)
            if d["kind"] == "hello":
                if not chat.empty:
                    return self._set(d, "skipped", "you already have a chat with them, so no hello", "info")
            else:
                verdict = self._still_current(d, chat)
                if verdict:
                    return self._set(d, *verdict)
            self._check_blocked()
            self.web.send_here(d["text"])
            self._check_blocked()
            self._remember_chat(chat)
            with self.store.transaction():
                self.store.execute("UPDATE drafts SET status = 'sent', sent_at = ?, updated_at = ?, note = NULL WHERE id = ?",
                                   (now_iso(), now_iso(), d["id"]))
                if d["thread_id"]:
                    self.store.execute("UPDATE threads SET last_from_me = 1 WHERE id = ?", (d["thread_id"],))
            cents = (getattr(self.web, "total_usd", 0.0) - spent_before) * 100
            self.store.log("good", f"Sent to {who}: {d['text']}" + (f" (about {cents:.1f} cents)" if cents >= 0.05 else ""))
            self.page_failures = 0
            if self.just_one:
                self.just_one = False
                self.pause("Sent one. Have a look at it in Instagram, then press Send everything I approved for the rest.")
                self.store.log("info", "Sent one so you can check it. Press Send everything I approved for the rest.")
        except DeliveryUnknown as e:
            self._set(d, "unsure", "not sure it went through; have a look in the chat before trying again", "warn")
            log.info("delivery unknown", extra={"outcome": str(e)[:100]})
        except Exception as e:
            self._handle(e, who=who, draft=d)

    def _open_chat(self, d: dict):
        if d["username"]:
            row = self.store.one("SELECT chat_url FROM people WHERE username = ?", (d["username"],))
            return self.web.open_chat(username=d["username"], known_url=row["chat_url"] if row else None)
        chat = self.web.open_chat(name=d["other_name"])
        # Learn their username from the chat, so next time the chat opens directly.
        if chat.username and re.fullmatch(r"[A-Za-z0-9._]{1,30}", chat.username):
            with self.store.transaction():
                self.store.execute("UPDATE drafts SET username = ? WHERE id = ?", (chat.username, d["id"]))
                if d["thread_id"]:
                    self.store.execute("UPDATE threads SET username = ? WHERE id = ?", (chat.username, d["thread_id"]))
        return chat

    def _remember_chat(self, chat) -> None:
        """Keep the chat's address, so the next message to them opens it directly (fewer steps, less cost)."""
        if chat.username and chat.url:
            with self.store.transaction():
                self.store.execute("INSERT OR IGNORE INTO people(username) VALUES (?)", (chat.username,))
                self.store.execute("UPDATE people SET chat_url = ? WHERE username = ?", (chat.url, chat.username))

    def _still_current(self, d: dict, chat) -> tuple | None:
        """None if it's fine to send the reply; otherwise (status, note, log kind)."""
        if chat.last_is_mine:
            return "skipped", "you've replied to them since the download", "info"
        if chat.empty or not (chat.their_last or chat.their_recent):
            return "draft", "couldn't see their messages in the chat, so it didn't send; have a look", "warn"
        if d["replying_to"].startswith("["):         # a photo or post: no text to compare, the side is enough
            return None
        if similar(chat.their_last, d["replying_to"]):
            return None
        if any(similar(t, d["replying_to"]) for t in chat.their_recent):
            return "draft", "they've written again since the download; check the reply still fits", "info"
        return "draft", "their last message on Instagram doesn't match the download, so it didn't send; have a look", "warn"

    # ------------------------------------------------------------------ unfollowing
    def unfollow_one(self, u: dict) -> None:
        self.store.execute("UPDATE unfollows SET tries = tries + 1 WHERE username = ?", (u["username"],))
        try:
            self._check_blocked()
            result = self.web.unfollow(u["username"])
            self._check_blocked()
            with self.store.transaction():
                self.store.execute("UPDATE unfollows SET status = 'done', done_at = ?, note = ? WHERE username = ?",
                                   (now_iso(), None if result == "unfollowed" else "you weren't following them", u["username"]))
                self.store.execute("UPDATE people SET i_follow = 0 WHERE username = ?", (u["username"],))
            self.store.log("good", f"Unfollowed @{u['username']}" if result == "unfollowed" else
                           f"@{u['username']}: you weren't following them")
            self.page_failures = 0
        except Exception as e:
            self._handle(e, who="@" + u["username"], unfollow=u)

    # ------------------------------------------------------------------ problems
    def _check_blocked(self) -> None:
        try:
            text = self.web.page.evaluate("() => document.body ? document.body.innerText.slice(0, 20000) : ''")
        except Exception:
            return
        if BLOCKED.search(text or ""):
            raise RateLimited("Instagram showed a 'try again later' message")

    def _handle(self, e: Exception, who: str | None, draft: dict | None = None, unfollow: dict | None = None) -> None:
        action, plain, wait = classify(e)
        log.info("problem", extra={"outcome": f"{action}: {plain[:120]}"})
        if action == "stop":
            self._requeue(draft, unfollow)
            self.pause("Stopped: " + plain)
            self.identity_ok = False
            self.store.log("problem", plain)
            self.notify("Instagram assistant stopped", plain)
            return
        if action == "cooldown":
            self._requeue(draft, unfollow)
            self.wait_until = time.time() + wait
            until = (datetime.now() + timedelta(seconds=wait)).strftime("%H:%M")
            self.status = f"Instagram asked for a break; carrying on at {until}"
            self.store.log("warn", plain)
            self.notify("Instagram assistant is taking a break", plain)
            return
        if action == "later":
            self._requeue(draft, unfollow)
            self.wait_until = time.time() + wait
            self.status = plain
            self.store.log("warn", plain)
            return
        if action == "skip":
            self._fail(draft, unfollow, "skipped", plain)
            self.store.log("info", f"Skipped {who}: {plain}")
            return
        # "page": the page didn't look as expected. Try this person once more later; if it keeps happening with
        # different people, Instagram probably changed something, so stop and say so.
        self.page_failures += 1
        tries = (draft or unfollow or {}).get("tries", 1)
        shot = "data/diagnostics"
        try:
            self.web.save_screenshot("sender-problem")
        except Exception:
            pass
        if tries >= 2:
            self._fail(draft, unfollow, "failed", plain)
            self.store.log("warn", f"Gave up on {who} after two tries: {plain}")
        else:
            self._requeue(draft, unfollow)
            self.store.log("warn", f"{who}: {plain}. Will try once more later.")
        if self.page_failures >= 3:
            msg = ("Instagram's page looked different three times in a row, so sending stopped to be safe. "
                   f"Run bash check.sh and send Claude the check.txt (pictures are in {shot}).")
            self.pause("Stopped: " + msg)
            self.store.log("problem", msg)
            self.notify("Instagram assistant stopped", msg)

    def _requeue(self, draft: dict | None, unfollow: dict | None) -> None:
        if draft:
            self.store.execute("UPDATE drafts SET status = 'approved', updated_at = ? WHERE id = ? AND status = 'sending'",
                               (now_iso(), draft["id"]))
        # Replies go before hellos, and anything already tried waits behind untried ones (ORDER BY tries).

    def _fail(self, draft: dict | None, unfollow: dict | None, status: str, note: str) -> None:
        if draft:
            self.store.execute("UPDATE drafts SET status = ?, note = ?, updated_at = ? WHERE id = ?",
                               (status, note, now_iso(), draft["id"]))
        if unfollow:
            self.store.execute("UPDATE unfollows SET status = ?, note = ? WHERE username = ?", (status, note, unfollow["username"]))

    def _set(self, d: dict, status: str, note: str, kind: str) -> None:
        self.store.execute("UPDATE drafts SET status = ?, note = ?, updated_at = ? WHERE id = ?", (status, note, now_iso(), d["id"]))
        who = ("@" + d["username"]) if d["username"] else d["other_name"]
        self.store.log(kind, f"{who}: {note}")
