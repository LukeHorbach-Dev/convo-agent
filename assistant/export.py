"""Reads Instagram's "Download your information" export (JSON format): who you follow, who follows you, and every
conversation. Takes one or more .zip files (Instagram splits big downloads) or an unzipped folder.

Handles the layouts Instagram has used: files at the top level or under your_instagram_activity/ and connections/,
following.json entries that carry the username in string_list_data.value, in title, or only in the href, and text
that Instagram writes as UTF-8 bytes escaped as Latin-1 ("Ã©" for "é", "ð\\x9f\\x98\\x82" for an emoji).
"""
from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from .store import Store, now_iso


class ExportError(Exception):
    """The file isn't an Instagram download this can read; the message says what to do."""


@dataclass
class Message:
    at: int                      # ms
    sender: str
    text: str


@dataclass
class Thread:
    id: str                      # folder name, e.g. "annawalsh_10224...": stable across downloads
    title: str
    participants: list[str]
    is_request: bool
    messages: list[Message] = field(default_factory=list)


@dataclass
class ExportData:
    me_username: str | None = None
    me_name: str | None = None
    following: dict[str, int | None] = field(default_factory=dict)    # username -> when you followed them
    followers: set[str] = field(default_factory=set)
    has_following: bool = False
    has_followers: bool = False
    threads: list[Thread] = field(default_factory=list)


def fix_text(s: str) -> str:
    """Instagram writes UTF-8 as Latin-1 escapes. Undo that; leave text that was already fine alone."""
    if not s:
        return s
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")


def _username_from(item: dict) -> str | None:
    """One entry of followers_*.json / following.json, in any of the shapes seen."""
    for d in item.get("string_list_data") or []:
        v = (d.get("value") or "").strip()
        if _USERNAME.match(v):
            return v.lower()
        m = re.search(r"instagram\.com/(?:_u/)?([A-Za-z0-9._]{1,30})/?$", d.get("href") or "")
        if m:
            return m.group(1).lower()
    t = (item.get("title") or "").strip()
    return t.lower() if _USERNAME.match(t) else None


def _timestamp(item: dict) -> int | None:
    for d in item.get("string_list_data") or []:
        if d.get("timestamp"):
            return int(d["timestamp"])
    return None


# Lines Instagram writes into chats that aren't messages from the person (with or without their name in front).
_IGNORE = re.compile(r"(?i)^(liked a message|reacted .{1,16} to (your|a) message|unsent a message|changed the (theme|group photo)\b.*"
                     r"|set (the|your|their|his|her) nickname\b.*|named the group\b.*)\.?$")
_ATTACHMENT = re.compile(r"(?i)^sent an attachment\.?$")
_CALL = re.compile(r"(?i)^(missed an? (video|audio|voice) (chat|call)|started an? (video|audio|voice) (chat|call)"
                   r"|(video|audio|voice) (chat|call) ended|call ended)\.?$")
# Tags Convo uses for things that aren't text. The AI is told it can't see these; voice messages and calls are
# left for Luke.
MEDIA_TAG = re.compile(r"\[(voice message|call|photo|video|gif|sticker|file|attachment|reel|post|shared a post)\b[^\]]*\]")


def _without_name(content: str, sender: str) -> str:
    for name in (sender, "You"):
        if name and content.lower().startswith(name.lower() + " "):
            return content[len(name) + 1:]
    return content


def _share_tag(share: dict) -> str:
    link = share.get("link") or ""
    kind = "reel" if "/reel" in link else "post"
    owner = (share.get("original_content_owner") or "").strip().lstrip("@")
    caption = re.sub(r"\s+", " ", fix_text(share.get("share_text") or "")).strip()
    caption = (caption[:140] + "...") if len(caption) > 140 else caption
    return f"[{kind}" + (f" by @{owner}" if owner else "") + (f": {caption}" if caption else "") + "]"


def _message_text(m: dict) -> str | None:
    """Plain text for a message, or a short tag for anything else ("[voice message]", "[reel by @x: caption]");
    None for things that aren't messages (likes, reactions, unsent messages, theme changes)."""
    if m.get("is_unsent"):
        return None
    sender = fix_text(m.get("sender_name") or "")
    content = fix_text(m.get("content") or "").strip()
    bare = _without_name(content, sender)
    share = m.get("share") if isinstance(m.get("share"), dict) else None
    if content and _IGNORE.match(bare):
        return None
    if m.get("call_duration") is not None or (content and _CALL.match(bare)):
        return "[call]"
    attachment_only = bool(content) and bool(_ATTACHMENT.match(bare))
    tag = None
    for key, t in (("audio_files", "[voice message]"), ("photos", "[photo]"), ("videos", "[video]"), ("gifs", "[gif]"),
                   ("sticker", "[sticker]"), ("files", "[file]")):
        if m.get(key):
            tag = t
            break
    if tag is None and share:
        tag = _share_tag(share)
    if content and not attachment_only:
        # Text typed with a shared reel or post is often just the post's address; keep what they wrote.
        if share and tag and content == (share.get("link") or "").strip():
            return tag
        return f"{content} {tag}" if tag else content
    if tag:
        return tag
    if attachment_only:
        return "[attachment]"
    if m.get("share") is not None:
        return "[post]"
    return None


_IGNORE_NAMED = re.compile(r"(?i)^[\w .'-]{0,40}\b(liked a message|reacted .{1,16} to (your|a) message|unsent a message)\.?$")


def is_ignorable(text: str, sender: str) -> bool:
    """For text saved by older versions: lines that aren't messages."""
    t = (text or "").strip()
    return bool(_IGNORE.match(_without_name(t, sender or "")) or _IGNORE_NAMED.match(t))


class _Source:
    """A uniform view over zips and folders: relative path -> bytes."""

    def __init__(self, paths: list[Path]):
        self.files: dict[str, tuple] = {}
        for p in paths:
            p = Path(p).expanduser()
            if p.is_dir():
                for f in p.rglob("*.json"):
                    self.files[str(f.relative_to(p)).replace("\\", "/")] = ("dir", f)
            elif p.suffix.lower() == ".zip" and p.exists():
                z = zipfile.ZipFile(p)
                for name in z.namelist():
                    if name.lower().endswith(".json"):
                        self.files[name] = ("zip", z, name)
            else:
                raise ExportError(f"{p.name} isn't a .zip or a folder")

    def names(self) -> list[str]:
        return list(self.files)

    def read(self, name: str):
        src = self.files[name]
        raw = src[1].read_bytes() if src[0] == "dir" else src[1].read(src[2])
        return json.loads(raw.decode("utf-8"))


def read_export(paths: list[Path]) -> ExportData:
    src = _Source(paths)
    names = src.names()
    if not names:
        raise ExportError("there's nothing readable in that download. When you request it, pick JSON, not HTML.")
    out = ExportData()

    for n in names:
        low = n.lower()
        base = low.rsplit("/", 1)[-1]
        if base == "personal_information.json":
            try:
                smd = src.read(n)["profile_user"][0]["string_map_data"]
                out.me_username = (smd.get("Username", {}).get("value") or "").lower() or None
                out.me_name = fix_text(smd.get("Name", {}).get("value") or "") or None
            except (KeyError, IndexError, TypeError):
                pass
        elif base == "following.json":
            data = src.read(n)
            items = data.get("relationships_following", []) if isinstance(data, dict) else data
            out.has_following = True
            for it in items:
                if u := _username_from(it):
                    out.following[u] = _timestamp(it)
        elif re.fullmatch(r"followers(_\d+)?\.json", base):
            data = src.read(n)
            items = data.get("relationships_followers", []) if isinstance(data, dict) else data
            out.has_followers = True
            for it in items:
                if u := _username_from(it):
                    out.followers.add(u)

    # Conversations: <...>/messages/inbox/<folder>/message_N.json and .../message_requests/...
    by_folder: dict[str, list[str]] = {}
    for n in names:
        m = re.search(r"(?:^|/)messages/(inbox|message_requests)/([^/]+)/message_\d+\.json$", n)
        if m:
            by_folder.setdefault(f"{m.group(1)}/{m.group(2)}", []).append(n)
    for key, files in by_folder.items():
        box, folder = key.split("/", 1)
        title, participants, msgs = "", [], []
        for f in sorted(files):
            d = src.read(f)
            title = title or fix_text(d.get("title") or "")
            participants = participants or [fix_text(p.get("name") or "") for p in d.get("participants", [])]
            for m in d.get("messages", []):
                text = _message_text(m)
                if text is None or not m.get("timestamp_ms"):
                    continue
                msgs.append(Message(int(m["timestamp_ms"]), fix_text(m.get("sender_name") or ""), text))
        msgs.sort(key=lambda x: x.at)
        out.threads.append(Thread(folder, title, participants, box == "message_requests", msgs))

    if not (out.has_following or out.has_followers or out.threads):
        raise ExportError("that download has no followers, following or messages in it. Request it again and tick "
                          "Messages and Followers and following, in JSON format.")
    if out.me_name is None and out.threads:
        # The name that appears in the most conversations is you.
        counts: dict[str, int] = {}
        for t in out.threads:
            for p in set(t.participants):
                counts[p] = counts.get(p, 0) + 1
        out.me_name = max(counts, key=counts.get) if counts else None
    return out


def _squash(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _folder_username(folder: str, known: set[str]) -> str | None:
    """Thread folders are named after the other account plus a number ("annawalsh_10224..."). Match the name part
    against usernames we know, ignoring dots and underscores. Only an exact, unique match counts."""
    prefix = _squash(re.sub(r"_\d+$", "", folder))
    if not prefix:
        return None
    hits = [u for u in known if _squash(u) == prefix]
    return hits[0] if len(hits) == 1 else None


def apply_export(store: Store, data: ExportData, me_username: str) -> dict:
    """Load a download into the store. Safe to repeat with every new download: messages are de-duplicated, and
    following/followers are replaced only when the download contains those lists."""
    me_name = data.me_name
    known = set(data.following) | data.followers | {r["username"] for r in store.all("SELECT username FROM people")}
    with store.transaction():
        if data.has_following:
            store.execute("UPDATE people SET i_follow = 0")
            for u, ts in data.following.items():
                store.execute("INSERT INTO people(username, i_follow, followed_at, updated_at) VALUES (?, 1, ?, ?) "
                              "ON CONFLICT(username) DO UPDATE SET i_follow = 1, followed_at = COALESCE(excluded.followed_at, followed_at), "
                              "updated_at = excluded.updated_at", (u, ts, now_iso()))
        if data.has_followers:
            store.execute("UPDATE people SET follows_me = 0")
            for u in data.followers:
                store.execute("INSERT INTO people(username, follows_me, updated_at) VALUES (?, 1, ?) "
                              "ON CONFLICT(username) DO UPDATE SET follows_me = 1, updated_at = excluded.updated_at", (u, now_iso()))
        new_msgs = 0
        for t in data.threads:
            others = [p for p in t.participants if p and p != me_name]
            is_group = len(set(others)) > 1
            username = None if is_group else _folder_username(t.id, known)
            if not t.messages:
                continue
            # Older versions saved some non-text messages differently ("[shared a post]", "Liked a message"):
            # bring those rows in line with how this version reads them.
            for r in store.all("SELECT at, sender, text FROM messages WHERE thread_id = ?", (t.id,)):
                if is_ignorable(r["text"], r["sender"]):
                    store.execute("DELETE FROM messages WHERE thread_id = ? AND at = ? AND sender = ? AND text = ?",
                                  (t.id, r["at"], r["sender"], r["text"]))
            for d in store.all("SELECT id, replying_to FROM drafts WHERE kind = 'reply' AND thread_id = ? "
                               "AND status IN ('draft', 'writing')", (t.id,)):
                if is_ignorable(d["replying_to"], ""):    # a draft answering a "liked a message" line
                    store.execute("DELETE FROM drafts WHERE id = ?", (d["id"],))
            for m in t.messages:
                if "[" in m.text:                     # a tag whose wording changed between versions
                    store.execute("DELETE FROM messages WHERE thread_id = ? AND at = ? AND sender = ? AND text != ? "
                                  "AND text LIKE '%[%'", (t.id, m.at, m.sender, m.text))
                cur = store.execute("INSERT OR IGNORE INTO messages(thread_id, at, from_me, sender, text) VALUES (?, ?, ?, ?, ?)",
                                    (t.id, m.at, int(m.sender == me_name), m.sender, m.text))
                new_msgs += cur.rowcount
            last = t.messages[-1]
            other_name = others[0] if others else t.title
            store.execute(
                "INSERT INTO threads(id, title, username, other_name, is_group, is_request, last_at, last_from_me, message_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET title = excluded.title, "
                "username = COALESCE(excluded.username, threads.username), other_name = excluded.other_name, "
                "is_group = excluded.is_group, is_request = excluded.is_request, last_at = excluded.last_at, "
                "last_from_me = excluded.last_from_me, message_count = excluded.message_count",
                (t.id, t.title, username, other_name, int(is_group), int(t.is_request), last.at, int(last.sender == me_name),
                 len(t.messages)))
            if username:
                store.execute("INSERT INTO people(username, display_name, updated_at) VALUES (?, ?, ?) "
                              "ON CONFLICT(username) DO UPDATE SET display_name = excluded.display_name", (username, other_name, now_iso()))
        summary = {"at": now_iso(), "following": len(data.following) if data.has_following else None,
                   "followers": len(data.followers) if data.has_followers else None,
                   "conversations": sum(1 for t in data.threads if t.messages), "new_messages": new_msgs,
                   "you": data.me_username or me_username, "your_name": me_name}
        store.set("last_import", summary)
    parts = []
    if summary["following"] is not None:
        parts.append(f"{summary['following']} people you follow")
    if summary["followers"] is not None:
        parts.append(f"{summary['followers']} followers")
    parts.append(f"{summary['conversations']} conversations ({new_msgs} new messages)")
    store.log("good", "Loaded your Instagram download: " + ", ".join(parts) + ".")
    if data.me_username and data.me_username != me_username.lower():
        store.log("problem", f"This download is for @{data.me_username}, but the app is set up for @{me_username}.")
    return summary


def find_downloads(folder: Path) -> list[Path]:
    """The newest Instagram download in a folder. Instagram names them instagram-<you>-<date>-<code>.zip and may split
    a big one into several parts with the same date; all parts from the newest date are returned."""
    zips = sorted(Path(folder).expanduser().glob("instagram-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not zips:
        return []
    newest = zips[0]
    date = re.search(r"\d{4}-\d{2}-\d{2}", newest.name)
    if not date:
        return [newest]
    return [z for z in zips if date.group(0) in z.name and abs(z.stat().st_mtime - newest.stat().st_mtime) < 6 * 3600]


def describe_export(paths: list[Path]) -> str:
    """What kinds of messages a download holds, without any of their words: how many of each shape (which fields
    Instagram filled in), and what Convo makes of them. Safe to share."""
    src = _Source(paths)
    shapes: dict[str, int] = {}
    kinds: dict[str, int] = {}
    threads = 0
    for n in src.names():
        if not re.search(r"(?:^|/)messages/(inbox|message_requests)/[^/]+/message_\d+\.json$", n):
            continue
        threads += 1
        for m in src.read(n).get("messages", []):
            keys = sorted(k for k in m if k not in ("sender_name", "timestamp_ms"))
            shape = ", ".join(keys) or "(nothing)"
            shapes[shape] = shapes.get(shape, 0) + 1
            text = _message_text(m)
            if text is None:
                kind = "left out (like, reaction, unsent, theme)"
            else:
                tags = [f"[{t}]" for t in MEDIA_TAG.findall(text)]
                kind = (("text + " if MEDIA_TAG.sub("", text).strip() else "") + tags[0]) if tags else "text"
            kinds[kind] = kinds.get(kind, 0) + 1
    lines = [f"Download check: {threads} chat files, {sum(kinds.values())} messages. No message words are shown.", "",
             "What Convo makes of them:"]
    lines += [f"  {n:>6}  {k}" for k, n in sorted(kinds.items(), key=lambda x: -x[1])]
    lines += ["", "Which fields Instagram filled in:"]
    lines += [f"  {n:>6}  {k}" for k, n in sorted(shapes.items(), key=lambda x: -x[1])[:40]]
    return "\n".join(lines)
