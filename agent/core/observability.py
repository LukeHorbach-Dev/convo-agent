"""Structured JSON logging with redaction, and simple counters exposed through the dashboard."""
from __future__ import annotations

import json
import logging
import re
import threading
from collections import Counter
from contextvars import ContextVar

_context: ContextVar[dict] = ContextVar("log_context", default={})

SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]+"),
    re.compile(r"(?i)(sessionid|csrftoken|ds_user_id|authorization|cookie|password|token)([\"'=:\s]+)([^\s\"',;]+)"),
]


def redact(text: str) -> str:
    text = SECRET_PATTERNS[0].sub("sk-ant-[REDACTED]", text)
    return SECRET_PATTERNS[1].sub(lambda m: f"{m.group(1)}{m.group(2)}[REDACTED]", text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                "component": record.name, "msg": redact(record.getMessage())}
        data.update(_context.get())
        for key in ("job_id", "account", "conversation", "operation_id", "category", "retry", "transition",
                    "decision", "duration_ms", "outcome", "run_id"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        if record.exc_info:
            data["error"] = redact(self.formatException(record.exc_info))[-2000:]
        return json.dumps(data, default=str)


def setup_logging(level: str = "INFO", path: str | None = None) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler: logging.Handler = logging.FileHandler(path) if path else logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(level)


def bind(**fields) -> None:
    _context.set({**_context.get(), **fields})


class Metrics:
    def __init__(self) -> None:
        self._c: Counter = Counter()
        self._lock = threading.Lock()

    def inc(self, name: str, n: int = 1) -> None:
        with self._lock:
            self._c[name] += n

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._c)


metrics = Metrics()
