"""Practice mode: the whole app on a pretend Instagram, so everything can be tried without touching the real one.

`python -m assistant practice` (or "Try it on practice Instagram" in the app) starts a second copy of the app on
port 8766 with its own data (data/practice/). It runs a small Instagram look-alike on this Mac with made-up people,
makes downloads from it in the real download format, and the browser window shows Claude (or a free stand-in)
working on it. People there write back a few seconds after you message them.

Only here is there an "automatic" switch: replies are approved and sent as soon as they're written. The real app
always waits for Luke's approval.
"""
from __future__ import annotations

import json
import random
import re
import shutil
import threading
import time
import zipfile
import zlib
from datetime import datetime
from itertools import count
from pathlib import Path
from types import SimpleNamespace

from agent.platform.mock import MockWorld

ME_NAME = "Luke"

# (username, name, bio, what they've written so far, whether Luke follows them)
PEOPLE = [
    ("maya.paints", "Maya Chen", "painter, coffee, long walks", ["hey :) saw your post about baking", "what do you usually make?"], True),
    ("tom.kicks", "Tom Riley", "Sunday league keeper", ["you still play football?", "we need a striker on sunday"], True),
    ("sofia.reads", "Sofia Marin", "books and bad puns", ["omg did you finish that book i said about",
                                                          "[reel by @bookclub: when the sequel finally drops]"], True),
    ("jake_runs", "Jake Patel", "running club Maastricht", ["morning run tomorrow at 7 if you're up for it", "[voice message]"], True),
    ("lena.cooks", "Lena Vos", "chef in training", ["hiii how's your week going?"], True),
    ("cash.quick", "Free Money", "DM for crypto tips", ["hey can you send me 50 on paypal, ill pay back double"], True),
    ("young.ella", "Ella", "", ["hey im 16 btw, you seem cool"], True),
    ("noah.films", "Noah Berg", "film student", [], True),
    ("iris.climbs", "Iris Kaya", "bouldering most days", [], True),
    ("ben.makes", "Ben Okafor", "woodwork + design", [], True),
    ("fan.account1", "Ruby", "", [], False),
]

REPLIES = ["haha nice :)", "sounds good to me", "oh cool, how come?", "yes! when are you free?", "that's fair",
           "omg same", "ahh i love that", "no way, tell me more"]


def seed(world: MockWorld) -> None:
    for u, name, bio, msgs, follow in PEOPLE:
        if follow:
            world.new_follower(u, name, bio)
        else:
            world.add_account(u, name, bio)
            world.new_fan(u)
        for m in msgs:
            world.incoming(u, m)


def answer_back(world: MockWorld, delay_s: float = 6.0) -> None:
    """People in the practice world write back a few seconds after Luke messages them."""
    deliver = world.deliver
    rnd = random.Random(7)

    def wrapped(thread_key: str, text: str) -> str:
        mid = deliver(thread_key, text)
        who = world.threads[thread_key]["username"]
        if who not in ("cash.quick", "young.ella"):
            threading.Timer(delay_s, lambda: world.incoming(who, rnd.choice(REPLIES))).start()
        return mid
    world.deliver = wrapped


def _as_instagram(text: str, name: str, moji) -> dict:
    """How Instagram's download writes a message: voice messages and shared reels the way real ones look."""
    if text == "[voice message]":
        return {"audio_files": [{"uri": "your_instagram_activity/messages/inbox/x/audio/1.mp4"}]}
    reel = re.fullmatch(r"\[reel by @([\w.]+): (.*)\]", text)
    if reel:
        return {"content": moji(f"{name} sent an attachment."), "share": {
            "link": f"https://www.instagram.com/reel/practice{zlib.crc32(reel.group(2).encode()) % 1000}/",
            "share_text": moji(reel.group(2)), "original_content_owner": reel.group(1)}}
    return {"content": moji(text)}


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def write_download(world: MockWorld, folder: Path) -> Path:
    """A download of the practice world, in the same format Instagram's "Download your information" uses."""
    folder.mkdir(parents=True, exist_ok=True)
    moji = lambda s: s.encode("utf-8").decode("latin-1")         # Instagram's escaping, as in real downloads
    with world.lock:
        following = [e for e in world.follower_events if e["username"] not in world.unfollowed]
        files = {
            "personal_information/personal_information/personal_information.json": {"profile_user": [{"string_map_data": {
                "Username": {"value": world.me}, "Name": {"value": ME_NAME}}}]},
            "connections/followers_and_following/following.json": {"relationships_following": [
                {"title": e["username"], "string_list_data": [{"href": f"https://www.instagram.com/_u/{e['username']}",
                                                               "timestamp": _ms(e["at"]) // 1000}]} for e in following]},
            "connections/followers_and_following/followers_1.json": [
                {"title": "", "string_list_data": [{"href": f"https://www.instagram.com/{u}", "value": u, "timestamp": 1}]}
                for u in [e["username"] for e in world.follower_events][:6] + world.fans],
        }
        for key, t in world.threads.items():
            if not t["messages"]:
                continue
            acct = world.accounts[t["username"]]
            folder_name = t["username"].replace(".", "").replace("_", "") + "_" + str(zlib.crc32(t["username"].encode()))
            files[f"your_instagram_activity/messages/inbox/{folder_name}/message_1.json"] = {
                "participants": [{"name": moji(acct.display_name)}, {"name": ME_NAME}], "title": moji(acct.display_name),
                "is_still_participant": True,
                "messages": [{"sender_name": moji(ME_NAME if m["dir"] == "out" else acct.display_name),
                              "timestamp_ms": _ms(m["at"]) + i, **_as_instagram(m["text"], acct.display_name, moji)}
                             for i, m in reversed(list(enumerate(t["messages"])))]}
    path = folder / f"instagram-{world.me}-{datetime.now():%Y-%m-%d-%H%M%S}-practice.zip"
    with zipfile.ZipFile(path, "w") as z:
        for name, data in files.items():
            z.writestr(name, json.dumps(data))
    return path


# ---------------------------------------------------------------------------------------------- the free stand-in
_ids = count(1)


def use(name, inp=None, computer=True):
    return SimpleNamespace(type="tool_use", id=f"toolu_standin_{next(_ids)}", name=name, input=inp or {},
                           toolset_name="computer" if computer else None)


def center(page, selector):
    box = page.locator(selector).filter(visible=True).first.bounding_box()
    return [int(box["x"] + box["width"] / 2), int(box["y"] + box["height"] / 2)]


class StandIn:
    """Plays Claude's part on the practice Instagram for free: it reads the page's code (which works there because
    it's our own page) and answers with the same clicks and reports Claude would. Shaped like the Anthropic client
    (client.messages.create), so the app can't tell the difference. Used by practice mode without real AI and by
    the tests. `misbehave` actions are tried first (tests use them to check what Claude isn't allowed to do)."""

    def __init__(self, hands_ref: dict, misbehave=None, pause_ms: int = 450):
        self.hands_ref, self.misbehave, self.pause_ms = hands_ref, list(misbehave or []), pause_ms
        self.calls: list[dict] = []
        self.messages = self
        self.unfollowed = False

    def create(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})      # as it was at this call
        if len(kw["messages"]) == 1:                                       # a new task
            self.unfollowed = False
        page = self.hands_ref["hands"].page
        task = kw["messages"][0]["content"][0]["text"]
        blocks = [self.misbehave.pop(0)] if self.misbehave else self.decide(page, task)
        return SimpleNamespace(content=blocks, stop_reason="tool_use", model="claude-haiku-5-5",
                               usage=SimpleNamespace(input_tokens=0, output_tokens=0, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0))

    def decide(self, page, task):
        page.wait_for_timeout(self.pause_ms)          # let the practice profile draw its buttons
        path = page.url.split("//", 1)[1].split("/", 1)[1]
        vis = lambda sel: page.locator(sel).filter(visible=True).count() > 0
        if task.startswith("Which Instagram account"):
            return [use("signed_in_as", {"username": self.hands_ref["hands"].me if vis(':text("Edit profile")') else ""}, False)]
        if task.startswith("Only look") and "chat list" in task:
            row = page.locator('[data-testid="thread-row"]').first.inner_text().split(" ")[0]
            return [use("top_chat", {"name": row}, False)]
        if task.startswith("Only look"):
            return [use("follow_state", {"i_follow": vis(':text-is("Following")')}, False)]
        if task.startswith("Unfollow"):
            if vis('[role=dialog] :text-is("Unfollow")'):
                self.unfollowed = True
                return [use("left_click", {"coordinate": center(page, '[role=dialog] :text-is("Unfollow")')}), use("screenshot")]
            if vis('#fb:text-is("Following")'):
                return [use("left_click", {"coordinate": center(page, "#fb")}), use("screenshot")]
            return [use("unfollow_finished", {"result": "unfollowed" if self.unfollowed else "was_not_following"}, False)]
        if path.startswith("direct/t/"):
            if not page.evaluate("() => document.activeElement && document.activeElement.isContentEditable"):
                return [use("left_click", {"coordinate": center(page, "[role=textbox]")}), use("screenshot")]
            msgs = page.evaluate("""() => [...document.querySelectorAll('[data-testid=message]')]
                                    .map(m => ({d: m.dataset.direction, t: m.textContent}))""")
            theirs = [m["t"] for m in msgs if m["d"] == "in"]
            user = page.locator('[data-testid="thread-username"]').first.inner_text()
            return [use("chat_is_open", {"username_shown": user, "name_shown": user, "chat_is_empty": not msgs,
                                         "last_message_is_mine": bool(msgs) and msgs[-1]["d"] == "out",
                                         "their_last_message": theirs[-1] if theirs else "",
                                         "their_recent_messages": theirs[-5:]}, False)]
        if path.startswith("direct/inbox"):
            name = task.split('"')[1]
            row = f'[data-testid="thread-row"]:text-matches("^{name} ")'
            if not vis(row):
                return [use("cannot_continue", {"reason": "not_found", "explanation": "no chat with that name"}, False)]
            return [use("left_click", {"coordinate": center(page, row)}), use("screenshot")]
        if vis('svg[aria-label="Expand"]'):
            return [use("left_click", {"coordinate": center(page, 'svg[aria-label="Expand"]')}), use("screenshot")]
        if vis('[data-testid="message-button"]'):
            return [use("left_click", {"coordinate": center(page, '[data-testid="message-button"]')}), use("screenshot")]
        if vis(':text("this page isn\'t available")'):
            return [use("cannot_continue", {"reason": "profile_unavailable", "explanation": "page isn't available"}, False)]
        return [use("go_to", {"where": "message_link"}, False)]


def practice_writer(system: str, user: str) -> dict:
    """Free stand-in for writing replies in practice mode (no AI): short, friendly, in Luke's style."""
    last = user.rsplit("THEM:", 1)[-1].split("\n", 1)[0].strip().lower()
    if any(w in last for w in ("paypal", "money", "crypto", "send me")):
        return _answer("", True, "they're asking for money", "asks for money", 0, "high")
    if "football" in last or "striker" in last:
        text = "Yeah still play, sunday works for me"
    elif "?" in last:
        text = "Not much, been baking a bit lately. You ?"
    else:
        text = "Sounds good, what about you ?"
    return _answer(text, False, "", "friendly chat", 2, "none")


def _answer(reply, needs_human, reason, summary, interest, concern):
    return {"reply": reply, "needs_human": needs_human, "reason": reason, "summary": summary, "interest": interest,
            "interest_why": "practice", "concern": concern, "concern_why": reason if concern != "none" else "",
            "facts_used": [], "memory_notes": [], "language": "English", "their_message_english": "", "reply_english": ""}


def prepare_folder(base: Path, real_data: Path) -> None:
    """Practice keeps its own data; it starts from a copy of Luke's About me so replies sound like him."""
    base.mkdir(parents=True, exist_ok=True)
    for name in ("profile.json", "never.txt"):
        if not (base / name).exists() and (real_data / name).exists():
            shutil.copy(real_data / name, base / name)


def wait_for_port(port: int, timeout_s: float = 25) -> bool:
    import urllib.request
    end = time.time() + timeout_s
    while time.time() < end:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as r:
                return r.status == 200
        except Exception:
            time.sleep(0.4)
    return False


class PracticeLLM:
    """The free writer for practice mode: replies from practice_writer, reports from a template."""
    name = "fake"

    def __init__(self):
        self.calls = 0

    def complete_json(self, system, user, schema) -> dict:
        self.calls += 1
        props = schema.get("properties", {})
        if props and set(props) <= {"instagram", "tiktok", "youtube"}:          # captions for a post
            out = {}
            for p in props:
                out[p] = ({"title": "A practice video title", "description": "A practice description in your voice.", "hashtags": ["practice"]}
                          if p == "youtube" else {"caption": f"A practice {p} caption in your voice", "hashtags": ["practice", "convo"]})
            return out
        if "overview" in schema.get("properties", {}):
            return {"overview": "A practice contact. They're friendly and easy to talk to.", "talks_about": ["everyday things"],
                    "common_ground": [], "open_questions": [], "careful": [], "next_idea": "hows your week going ?"}
        return practice_writer(system if isinstance(system, str) else "\n".join(system), user)


class World:
    """The practice Instagram: the made-up world, its website on this Mac, and downloads made from it."""

    def __init__(self, me: str):
        from agent.platform.mock_site import SiteServer
        from agent.platform.sites import mock_site_profile
        self.world = MockWorld(me)
        seed(self.world)
        answer_back(self.world)
        port = _free_port()
        self.server = SiteServer(self.world, port).start()
        self.site = mock_site_profile(self.server.url)

    def new_download(self, folder: Path) -> Path:
        for old in folder.glob("instagram-*-practice.zip"):
            old.unlink()
        return write_download(self.world, folder)

    def stop(self) -> None:
        self.server.stop()


def _free_port() -> int:
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
