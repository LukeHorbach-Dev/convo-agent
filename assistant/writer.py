"""Writing replies overnight at half price, through Anthropic's Message Batches service.

The evening download goes in, every chat waiting on Luke is sent as one batch, and the answers usually come back
within the hour (always within 24 hours) at 50% of the normal price. While a batch is out, each chat has a
placeholder draft ("being written"), so nothing is written twice; the answers fill those placeholders in through
the same checks as replies written straight away (brain.finish_reply). A batch survives the app restarting: its
id and the jobs are kept in the store.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Iterator

from pydantic import ValidationError

from agent.core.errors import AgentError
from agent.intel.llm import cost_usd
from agent.intel.profile import Profile
from agent.intel.reasoning import Drafter

from .brain import THINK_SCHEMA, fail_reply, finish_reply, own_messages, plan_replies, prompt_for, summarise
from .store import Store, now_iso

log = logging.getLogger("writer")

GIVE_UP_S = 25 * 3600           # the service finishes or expires every batch within 24 hours

# Rough size of one reply request: the prompt (instructions, facts, his own messages, the chat) and the answer
# (with a little thinking). Used only to show estimates before anything is spent.
TYPICAL_IN, TYPICAL_OUT = 3500, 500


def estimate_usd(model: str, overnight: bool) -> float:
    from agent.intel.llm import PRICES
    price = next((v for k, v in PRICES.items() if model.startswith(k)), PRICES["claude-opus-5-5"])
    usd = (TYPICAL_IN * price[0] + TYPICAL_OUT * price[1]) / 1_000_000
    return usd / 2 if overnight else usd


class RealBatches:
    """The Message Batches service, through the AI provider's client."""

    def __init__(self, provider):
        self.provider = provider
        self.client = provider.client

    def submit(self, requests: list[dict]) -> str:
        return self.client.messages.batches.create(requests=requests).id

    def status(self, batch_id: str) -> str:
        return self.client.messages.batches.retrieve(batch_id).processing_status   # in_progress | canceling | ended

    def results(self, batch_id: str) -> Iterator[tuple[str, str, Any]]:
        for r in self.client.messages.batches.results(batch_id):
            kind = r.result.type                      # succeeded | errored | canceled | expired
            yield r.custom_id, kind, (r.result.message if kind == "succeeded" else getattr(r.result, "error", None))

    def cancel(self, batch_id: str) -> None:
        self.client.messages.batches.cancel(batch_id)

    def parse(self, payload) -> tuple[dict, float]:
        return self.provider.parse(payload), cost_usd(payload.model, payload.usage) / 2


class FakeBatches:
    """Same shape, answered at once by a FakeLLM: for tests and the practice mode without an AI key."""

    def __init__(self, llm, rounds: int = 1):
        self.llm, self.rounds = llm, rounds
        self.batches: dict[str, dict] = {}

    def submit(self, requests: list[dict]) -> str:
        bid = f"msgbatch_fake_{len(self.batches) + 1}"
        self.batches[bid] = {"requests": requests, "polls": 0, "cancelled": False}
        return bid

    def status(self, batch_id: str) -> str:
        b = self.batches[batch_id]
        b["polls"] += 1
        return "ended" if b["polls"] >= self.rounds or b["cancelled"] else "in_progress"

    def results(self, batch_id: str):
        b = self.batches[batch_id]
        for r in b["requests"]:
            if b["cancelled"]:
                yield r["custom_id"], "canceled", None
                continue
            p = r["params"]
            try:
                yield r["custom_id"], "succeeded", self.llm.complete_json([x["text"] for x in p["system"]],
                                                                         p["messages"][0]["content"], THINK_SCHEMA)
            except AgentError as e:
                yield r["custom_id"], "errored", str(e)

    def cancel(self, batch_id: str) -> None:
        self.batches[batch_id]["cancelled"] = True

    def parse(self, payload) -> tuple[dict, float]:
        return payload, 0.0


class Overnight:
    def __init__(self, store: Store, provider, batches):
        self.store, self.provider, self.batches = store, provider, batches

    @property
    def pending(self) -> dict | None:
        return self.store.get("overnight")

    def submit(self, profile: Profile, never: set[str], *, window_days: int, min_age: int) -> dict:
        """Plan the waiting chats and send the AI ones as one batch. Returns counts."""
        jobs, out = plan_replies(self.store, profile, never, window_days=window_days, manual=False, min_age=min_age)
        out["queued"] = 0
        if not jobs:
            if out["skipped"]:
                summarise(self.store, out, False)
            return out
        drafter = Drafter(profile, self.provider)
        own = own_messages(self.store)
        requests, saved = [], {}
        for i, job in enumerate(jobs):
            system, user = prompt_for(drafter, profile, job, own)
            params = self.provider.params(system, user, THINK_SCHEMA, fallbacks=False) if hasattr(self.provider, "params") \
                else {"system": [{"type": "text", "text": t} for t in system], "messages": [{"role": "user", "content": user}]}
            cid = f"r{i}"
            requests.append({"custom_id": cid, "params": params})
            saved[cid] = {k: job[k] for k in ("base", "context", "theirs", "thread")}
        batch_id = self.batches.submit(requests)
        with self.store.transaction():
            for job in saved.values():
                b = job["base"]
                self.store.execute(
                    "INSERT OR IGNORE INTO drafts(kind, username, thread_id, other_name, replying_to, replying_at, text, status, "
                    "note, context, created_at, updated_at) VALUES ('reply', ?, ?, ?, ?, ?, '', 'writing', ?, '{}', ?, ?)",
                    (b["username"], b["thread_id"], b["other_name"], b["replying_to"], b["replying_at"],
                     "being written overnight (half price)", now_iso(), now_iso()))
            self.store.set("overnight", {"id": batch_id, "at": time.time(), "jobs": saved, "skipped": out["skipped"]})
        out["queued"] = len(saved)
        self.store.log("info", f"Sent {len(saved)} chats to be written overnight at half price. They're usually ready "
                               "within the hour, and always by tomorrow.")
        return out

    def check(self, profile: Profile) -> str:
        """Collect the answers if they're back: 'none' (no batch out), 'waiting', or 'done'."""
        p = self.pending
        if not p:
            return "none"
        try:
            status = self.batches.status(p["id"])
        except Exception as e:                        # offline or the service is busy: try again next time
            log.info("batch status unavailable", extra={"outcome": str(e)[:120]})
            return "waiting"
        if status != "ended":
            if time.time() - p["at"] > GIVE_UP_S:
                try:
                    self.batches.cancel(p["id"])
                except Exception:
                    pass
            return "waiting"
        out = {"drafted": 0, "for_you": 0, "skipped": p.get("skipped", 0), "failed": 0}
        seen = set()
        for cid, kind, payload in self.batches.results(p["id"]):
            job = p["jobs"].get(cid)
            if job is None:
                continue
            seen.add(cid)
            if kind != "succeeded":
                fail_reply(self.store, job, "the overnight service " + ("ran out of time" if kind == "expired" else "had a problem"))
                out["failed"] += 1
                continue
            try:
                raw, usd = self.batches.parse(payload)
                self.store.add_spend(usd, "drafts")
                out["for_you"] += finish_reply(self.store, profile, job, raw)
                out["drafted"] += 1
            except (AgentError, ValidationError) as e:
                fail_reply(self.store, job, str(e).splitlines()[0])
                out["failed"] += 1
        for cid, job in p["jobs"].items():            # anything the service didn't answer
            if cid not in seen:
                fail_reply(self.store, job, "no answer came back")
                out["failed"] += 1
        self.store.execute("DELETE FROM state WHERE key = 'overnight'")
        summarise(self.store, out, False)
        return "done"

    def cancel(self) -> int:
        """Stop waiting for the batch; its chats go back to waiting (so they can be written straight away)."""
        p = self.pending
        if not p:
            return 0
        try:
            self.batches.cancel(p["id"])
        except Exception:
            pass
        n = self.store.execute("DELETE FROM drafts WHERE kind = 'reply' AND status = 'writing'").rowcount
        self.store.execute("DELETE FROM state WHERE key = 'overnight'")
        self.store.log("info", f"Stopped waiting for {n} overnight replies.")
        return n
