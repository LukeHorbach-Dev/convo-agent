"""Self-check (`python -m assistant check`): tries every website step the assistant uses, on the real account,
through Claude's eyes, without sending anything or unfollowing anyone. Writes data/diagnostics/check.txt."""
from __future__ import annotations

import time

from .store import Store


def run_check(hands, store: Store | None) -> str:
    me = hands.me
    results, details, ctx = [], [], {}

    def step(name, fn):
        t = time.monotonic()
        try:
            note = fn() or ""
            ok = True
        except Exception as e:
            note, ok = f"{type(e).__name__}: {str(e).splitlines()[0][:200]}", False
        spend = hands.last
        cost = f", {spend.usd * 100:.2f} cents" if spend and spend.usd else ""
        results.append((name, ok, f"{note} ({time.monotonic() - t:.0f}s{cost})"))
        if spend is not None:
            ctx["usd"] = ctx.get("usd", 0.0) + spend.usd
            if not ok:
                details.append(f"== {name}: what Claude did")
                details += ["  " + line for line in spend.log[-40:]]
        hands.last = None

    def signed_in():
        who = hands.whoami()
        assert who.lower() == me.lower(), f"signed in as @{who}, not @{me}"
        return f"signed in as @{who}"
    step("1 signed in", signed_in)

    def inbox():
        ctx["top"] = hands.top_chat_name()
        assert ctx["top"], "couldn't read the chat list"
        return f"the top chat is {ctx['top']!r}"
    step("2 messages page", inbox)

    # Someone you follow and have a chat with, from the download if it's loaded.
    who = None
    if store is not None:
        row = store.one("SELECT p.username FROM people p JOIN threads t ON t.username = p.username WHERE p.i_follow = 1 "
                        "AND t.is_group = 0 ORDER BY t.last_at DESC LIMIT 1")
        who = row["username"] if row else None

    def open_chat():
        chat = hands.open_chat(username=who) if who else hands.open_chat(name=ctx.get("top") or "")
        ctx["chat"] = chat
        ctx["who"] = who or chat.username
        mine = "you" if chat.last_is_mine else "them"
        return (f"{'@' + chat.username if chat.username else chat.name}: "
                + ("no messages yet" if chat.empty else f"newest message is from {mine}; their last: {chat.their_last[:60]!r}"))
    step("3 open a chat" + (" by username" if who else " from the inbox"), open_chat)

    def typing():
        if "chat" not in ctx:
            raise RuntimeError("no chat open (step 3 didn't pass)")
        how = hands.try_typing()
        return f"typed 'test' in the message box ({how}) and deleted it; nothing was sent"
    step("4 message box", typing)

    def by_name():
        chat = ctx.get("chat")
        if chat is None or not chat.name:
            raise RuntimeError("no chat name to try (step 3 didn't pass)")
        hands.open_chat(name=chat.name)
        return f"found {chat.name!r} in the chat list and opened it"
    step("5 open a chat by its name", by_name)

    def follow_state():
        u = ctx.get("who")
        if not u:
            raise RuntimeError("no one to try: load a download first")
        follows = hands.do_i_follow(u)
        return f"@{u}: the profile says {'Following' if follows else 'Follow'} (nothing pressed)"
    step("6 profile button (not pressed)", follow_state)

    ok = sum(1 for _, p, _ in results if p)
    lines = [f"Self-check: {ok} of {len(results)} steps passed. Nothing was sent and nobody was unfollowed.",
             f"AI cost of this check: about {ctx.get('usd', 0.0) * 100:.1f} cents.", ""]
    lines += [f"{'PASS' if p else 'FAIL'}  {n}: {note}" for n, p, note in results]
    return "\n".join(lines + ([""] + details if details else []))
