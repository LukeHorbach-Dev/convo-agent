"""Pluggable LLM providers. Providers return parsed JSON or raise ModelFailure (never a raw SDK error)."""
from __future__ import annotations

import json
import time
from collections import deque
from typing import Any, Callable, Protocol

from ..core.config import LLMConfig
from ..core.errors import ErrorCategory, InterventionRequired, ModelFailure


class LLMProvider(Protocol):
    name: str

    def complete_json(self, system: str, user: str, schema: dict) -> dict: ...
    def health(self) -> dict: ...


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, cfg: LLMConfig):
        import anthropic
        self._anthropic = anthropic
        key = cfg.api_key.get_secret_value() if cfg.api_key else None
        # max_retries=0: retries are owned by the RecoveryManager so they are bounded and audited.
        self.client = anthropic.Anthropic(api_key=key, timeout=cfg.timeout_s, max_retries=0)
        self.cfg = cfg

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        a = self._anthropic
        try:
            resp = self.client.messages.create(
                model=self.cfg.model, max_tokens=self.cfg.max_tokens, system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
                extra_body={"fallbacks": "default"},
            )
        except a.AuthenticationError as e:
            raise InterventionRequired(f"model provider rejected the API key: {e}") from e
        except a.RateLimitError as e:
            raise ModelFailure(f"model provider rate limited: {e}", retry_after_s=60) from e
        except (a.APITimeoutError, a.APIConnectionError) as e:
            raise ModelFailure(f"model provider unreachable: {e}") from e
        except a.APIStatusError as e:
            if e.status_code >= 500:
                raise ModelFailure(f"model provider error {e.status_code}") from e
            raise ModelFailure(f"model request rejected ({e.status_code}): {e}", category=ErrorCategory.PERMANENT) from e
        if resp.stop_reason == "refusal":
            raise ModelFailure("model declined to draft this message", category=ErrorCategory.OPERATOR_INTERVENTION_REQUIRED)
        if resp.stop_reason == "max_tokens":
            raise ModelFailure("model output truncated")
        text = next((b.text for b in resp.content if b.type == "text"), None)
        if text is None:
            raise ModelFailure("model returned no text block")
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ModelFailure(f"model returned invalid JSON: {e}") from e

    def health(self) -> dict:
        return {"ok": True, "provider": self.name, "model": self.cfg.model, "checked": "config only"}


class FakeLLM:
    """Deterministic provider for tests and the mock demo. Script behaviours with `queue`:
    a dict (returned), 'timeout', 'malformed', or a callable(system, user) -> dict."""
    name = "fake"

    def __init__(self, responder: Callable[[str, str], dict] | None = None):
        self.queue: deque[Any] = deque()
        self.calls: list[tuple[str, str]] = []
        self.responder = responder or self.default_responder
        self.delay_s = 0.0

    def complete_json(self, system: str, user: str, schema: dict) -> dict:
        self.calls.append((system, user))
        if self.delay_s:
            time.sleep(self.delay_s)
        step = self.queue.popleft() if self.queue else None
        if step == "timeout":
            raise ModelFailure("fake provider timed out")
        if step == "malformed":
            return {"unexpected": "shape"}
        if callable(step):
            return step(system, user)
        if isinstance(step, dict):
            return step
        return self.responder(system, user)

    @staticmethod
    def default_responder(system: str, user: str) -> dict:
        last = user.rsplit("THEM:", 1)[-1].split("\n", 1)[0].strip().lower()
        if "?" in last and ("you" in last or "your" in last):
            text = "Not much just finishing up uni atm, hby?"
            facts = ["f_uni"]
        elif any(w in last for w in ("ski", "snow")):
            text = "Ahh nice, I've been skiing a few times too, where did you go?"
            facts = ["f_ski"]
        else:
            text = "Sounds interesting, how did that start?"
            facts = []
        return {"reply": text, "intent": "continue conversation", "facts_used": facts,
                "needs_human": False, "reason": "fake provider", "memory_notes": [],
                "language": "English", "their_message_english": last, "reply_english": text}

    def health(self) -> dict:
        return {"ok": True, "provider": self.name}


def build_provider(cfg: LLMConfig) -> LLMProvider:
    return AnthropicProvider(cfg) if cfg.provider == "anthropic" else FakeLLM()
