"""SQLite access: one connection, serialized by a lock, explicit transactions, versioned migrations."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Any, Iterator


def now() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    return (dt or now()).isoformat(timespec="milliseconds")


def parse(ts: str | None) -> datetime | None:
    return datetime.fromisoformat(ts) if ts else None


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class Database:
    """Thread-safe wrapper. All writes go through transaction() so a failure leaves no partial state."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        self._depth = 0
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute("PRAGMA busy_timeout=10000")

    # Hook for fault injection in tests: called before every write statement.
    def before_write(self, sql: str) -> None:  # pragma: no cover - overridden in tests
        return None

    @contextmanager
    def transaction(self) -> Iterator["Database"]:
        with self._lock:
            outer = self._depth == 0
            if outer:
                self._conn.execute("BEGIN IMMEDIATE")
            self._depth += 1
            try:
                yield self
            except BaseException:
                self._depth -= 1
                if outer:
                    self._conn.execute("ROLLBACK")
                raise
            else:
                self._depth -= 1
                if outer:
                    self._conn.execute("COMMIT")

    def execute(self, sql: str, params: tuple | dict = ()) -> sqlite3.Cursor:
        with self._lock:
            if not sql.lstrip().upper().startswith("SELECT"):
                self.before_write(sql)
            return self._conn.execute(sql, params)

    def one(self, sql: str, params: tuple | dict = ()) -> sqlite3.Row | None:
        return self.execute(sql, params).fetchone()

    def all(self, sql: str, params: tuple | dict = ()) -> list[sqlite3.Row]:
        return self.execute(sql, params).fetchall()

    def scalar(self, sql: str, params: tuple | dict = ()) -> Any:
        row = self.one(sql, params)
        return row[0] if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- migrations -------------------------------------------------------
    def migrate(self) -> int:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
            applied = {r[0] for r in self._conn.execute("SELECT version FROM schema_version")}
            files = sorted(p for p in resources.files("agent.migrations").iterdir() if p.name.endswith(".sql"))
            for f in files:
                version = int(f.name.split("_", 1)[0])
                if version in applied:
                    continue
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    for stmt in f.read_text().split(";\n"):
                        if stmt.strip():
                            self._conn.execute(stmt)
                    self._conn.execute("INSERT INTO schema_version VALUES (?, ?)", (version, iso()))
                    self._conn.execute("COMMIT")
                except Exception:
                    self._conn.execute("ROLLBACK")
                    raise
            return max([*applied, *(int(f.name.split("_", 1)[0]) for f in files)], default=0)

    # --- small helpers used everywhere -----------------------------------
    def get_state(self, key: str, default: Any = None) -> Any:
        v = self.scalar("SELECT value FROM app_state WHERE key = ?", (key,))
        return json.loads(v) if v is not None else default

    def set_state(self, key: str, value: Any) -> None:
        self.execute("INSERT INTO app_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                     (key, json.dumps(value)))

    def audit(self, actor: str, action: str, detail: dict | str | None = None) -> None:
        self.execute("INSERT INTO audit(at, actor, action, detail) VALUES (?, ?, ?, ?)",
                     (iso(), actor, action, json.dumps(detail) if isinstance(detail, dict) else detail))
