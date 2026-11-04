"""Everything the assistant knows, in one SQLite file (data/assistant.db).

people    one row per Instagram account seen in the download (who you follow, who follows you, who you chat with)
threads   one row per conversation in the download
messages  the messages of each conversation
drafts    hellos and replies: draft -> approved -> sending -> sent / failed / skipped / unsure
unfollows people queued to unfollow: queued -> done / failed / skipped
activity  a plain-English log of what happened, shown on the dashboard
"""
from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS people (
    username      TEXT PRIMARY KEY,
    display_name  TEXT,
    i_follow      INTEGER NOT NULL DEFAULT 0,
    follows_me    INTEGER NOT NULL DEFAULT 0,
    followed_at   INTEGER,                 -- when you followed them (seconds), from the download
    summary       TEXT,                    -- one line from the AI
    interest      INTEGER,                 -- 0-3, how much Luke would probably enjoy talking to them
    interest_why  TEXT,
    concern       TEXT,                    -- none | low | high
    concern_why   TEXT,
    note          TEXT,                    -- Luke's own note
    memory        TEXT NOT NULL DEFAULT '[]',  -- JSON list of things they said / open questions
    updated_at    TEXT
);
CREATE TABLE IF NOT EXISTS threads (
    id            TEXT PRIMARY KEY,        -- folder name in the download
    title         TEXT,
    username      TEXT,                    -- the other person, when it could be worked out
    other_name    TEXT,                    -- their display name as the download shows it
    is_group      INTEGER NOT NULL DEFAULT 0,
    is_request    INTEGER NOT NULL DEFAULT 0,
    last_at       INTEGER,                 -- ms
    last_from_me  INTEGER,
    message_count INTEGER
);
CREATE TABLE IF NOT EXISTS messages (
    thread_id     TEXT NOT NULL,
    at            INTEGER NOT NULL,        -- ms
    from_me       INTEGER NOT NULL,
    sender        TEXT,
    text          TEXT NOT NULL,
    PRIMARY KEY (thread_id, at, sender, text)
);
CREATE TABLE IF NOT EXISTS drafts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    kind          TEXT NOT NULL,           -- hello | reply
    username      TEXT,
    thread_id     TEXT,
    other_name    TEXT,
    replying_to   TEXT,                    -- the message of theirs being answered (for the staleness check)
    replying_at   INTEGER,
    text          TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'draft',
    note          TEXT,                    -- why it needs a look, or what went wrong
    context       TEXT NOT NULL DEFAULT '{}',
    tries         INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    sent_at       TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_hello ON drafts(username) WHERE kind = 'hello';
CREATE UNIQUE INDEX IF NOT EXISTS one_reply ON drafts(thread_id, replying_at) WHERE kind = 'reply';
CREATE TABLE IF NOT EXISTS unfollows (
    username      TEXT PRIMARY KEY,
    status        TEXT NOT NULL DEFAULT 'queued',
    note          TEXT,
    tries         INTEGER NOT NULL DEFAULT 0,
    queued_at     TEXT NOT NULL,
    done_at       TEXT
);
CREATE TABLE IF NOT EXISTS activity (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    at            TEXT NOT NULL,
    kind          TEXT NOT NULL,           -- info | good | warn | problem
    text          TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def iso_ago(**kw) -> str:
    return (datetime.now(timezone.utc) - timedelta(**kw)).isoformat(timespec="seconds")


# Numbered, append-only changes to the database. Never edit a step that has shipped; add a new one.
MIGRATIONS: list[tuple[int, str]] = [
    (1, """
CREATE TABLE IF NOT EXISTS account_groups (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL UNIQUE,
    color         TEXT,
    description   TEXT,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    platform      TEXT NOT NULL,           -- instagram | tiktok | youtube (more can be added: see platforms.py)
    handle        TEXT NOT NULL,
    display_name  TEXT,
    role          TEXT NOT NULL DEFAULT 'main',      -- main | launch | other
    niche         TEXT,
    purpose       TEXT,
    notes         TEXT,
    group_id      INTEGER REFERENCES account_groups(id) ON DELETE SET NULL,
    referral_link TEXT,
    connection    TEXT NOT NULL DEFAULT 'none',      -- none | download | browser | api (comma-separated)
    status        TEXT NOT NULL DEFAULT 'not_connected',
    status_note   TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    UNIQUE (platform, handle)
);
CREATE TABLE IF NOT EXISTS snapshots (
    account_id    INTEGER NOT NULL,
    at            TEXT NOT NULL,           -- when the numbers were true
    source        TEXT NOT NULL,           -- download | api
    followers     INTEGER,
    following     INTEGER,
    posts         INTEGER,
    extra         TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (account_id, at, source)
)
"""),
]


class Store:
    def __init__(self, path: Path | str):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        # Added later: chat_url (3.1, where each person's chat is, learned the first time it's opened) and report
        # (4.0, the AI write-up of them).
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(people)")}
        for col in ("chat_url", "report"):
            if col not in have:
                self._conn.execute(f"ALTER TABLE people ADD COLUMN {col} TEXT")
        self.path = None if str(path) == ":memory:" else Path(path)
        self._migrate()

    # ---------------------------------------------------------------- versions of the database, with backups
    def _migrate(self) -> None:
        """Bring the database up to date, one numbered step at a time. Before any step changes a real database file,
        a full copy is saved in data/backups/ (so nothing Luke has is ever lost to an upgrade)."""
        version = int(self.scalar("SELECT value FROM state WHERE key = 'schema_version'") or 0)
        pending = [(n, sql) for n, sql in MIGRATIONS if n > version]
        if not pending:
            return
        if self.path is not None and (version > 0 or self._has_data()):
            self.backup(f"before-update-{pending[0][0]}")
        for n, sql in pending:
            with self._lock:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
                        self._conn.execute(stmt)
                    self._conn.execute("INSERT INTO state(key, value) VALUES ('schema_version', ?) "
                                       "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (str(n),))
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise

    def _has_data(self) -> bool:
        return bool(self.scalar("SELECT COUNT(*) FROM drafts") or self.scalar("SELECT COUNT(*) FROM people"))

    def backup(self, label: str = "manual", keep: int = 20) -> Path | None:
        """A complete, consistent copy of the database in data/backups/. Returns its path (None for a memory store)."""
        if self.path is None:
            return None
        folder = self.path.parent / "backups"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        out = folder / f"{self.path.stem}-{stamp}-{re.sub(r'[^a-z0-9-]', '', label.lower())[:40]}.db"
        dest = sqlite3.connect(str(out))
        try:
            with self._lock:
                self._conn.backup(dest)
        finally:
            dest.close()
        copies = sorted(folder.glob(f"{self.path.stem}-*.db"))
        for old in copies[:-keep]:
            old.unlink()
        return out

    def backups(self) -> list[dict]:
        if self.path is None:
            return []
        folder = self.path.parent / "backups"
        return [{"name": p.name, "bytes": p.stat().st_size, "at": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")}
                for p in sorted(folder.glob(f"{self.path.stem}-*.db"), reverse=True)] if folder.exists() else []

    # ---------------------------------------------------------------- basics
    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, rows: list) -> None:
        with self._lock:
            self._conn.executemany(sql, rows)

    def one(self, sql: str, params: tuple | dict = ()) -> dict | None:
        with self._lock:
            r = self._conn.execute(sql, params).fetchone()
            return dict(r) if r else None

    def all(self, sql: str, params: tuple | dict = ()) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        with self._lock:
            r = self._conn.execute(sql, params).fetchone()
            return r[0] if r else None

    def transaction(self):
        store = self

        class _Tx:
            def __enter__(self):
                store._lock.acquire()
                store._conn.execute("BEGIN IMMEDIATE")

            def __exit__(self, exc_type, *_):
                try:
                    store._conn.execute("ROLLBACK" if exc_type else "COMMIT")
                finally:
                    store._lock.release()
        return _Tx()

    def get(self, key: str, default: Any = None) -> Any:
        v = self.scalar("SELECT value FROM state WHERE key = ?", (key,))
        return json.loads(v) if v is not None else default

    def set(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value)))

    def log(self, kind: str, text: str) -> None:
        self.execute("INSERT INTO activity(at, kind, text) VALUES (?, ?, ?)", (now_iso(), kind, text))
        self.execute("DELETE FROM activity WHERE id < (SELECT MAX(id) - 2000 FROM activity)")

    def add_spend(self, usd: float, counter: str) -> None:
        """Add to the AI spend shown on the dashboard: total, per day (31 days), and how many drafts / website steps."""
        if not usd:
            return
        with self.transaction():
            spend = self.get("ai_spend", {}) or {}
            day = now_iso()[:10]
            spend["total"] = round(spend.get("total", 0.0) + usd, 6)
            spend[day] = round(spend.get(day, 0.0) + usd, 6)
            spend[counter] = spend.get(counter, 0) + 1
            spend[counter + "_usd"] = round(spend.get(counter + "_usd", 0.0) + usd, 6)
            keep = ("total", "drafts", "drafts_usd", "web", "web_usd", "reports", "reports_usd", "ask", "ask_usd")
            self.set("ai_spend", {k: v for k, v in spend.items() if k in keep or k >= iso_ago(days=31)[:10]})

    def spent_today(self) -> float:
        return (self.get("ai_spend", {}) or {}).get(now_iso()[:10], 0.0)

    # ---------------------------------------------------------------- counts used in several places
    def sent_today(self) -> int:
        return self.scalar("SELECT COUNT(*) FROM drafts WHERE status = 'sent' AND sent_at >= ?", (iso_ago(days=1),))

    def unfollowed_today(self) -> int:
        return self.scalar("SELECT COUNT(*) FROM unfollows WHERE status = 'done' AND done_at >= ?", (iso_ago(days=1),))

    def close(self) -> None:
        self._conn.close()
