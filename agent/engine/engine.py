"""The processing engine: ingestion, job handling, delivery, reconciliation and failure handling.

Ordering rules that guarantee correctness:
  * Every external side effect (a send) is preceded by a committed outbox row in `attempting` and a
    delivery_attempts row. If the process dies at any point after that commit, restart sees the attempt
    and reconciles instead of resending.
  * Nothing is marked done (intro_status = sent, job completed) unless the platform confirmed it.
  * Before every consequential action Controls.guard() runs, so a pause or emergency stop wins.
"""
from __future__ import annotations

import hashlib
import json
import re
import logging
import time
import uuid
from datetime import timedelta

from ..core.config import Mode, Settings
from ..core.db import Database, iso, new_id, now, parse
from ..core.errors import (AmbiguousState, AuthenticationRequired, BrowserCrashed, DataIntegrityError, DeliveryUnknown,
                           ErrorCategory, LeaseLost, OperatorStop, PermissionRestricted, PolicyViolation, QueueFull)
from ..core.jobs import Job, JobQueue
from ..core.observability import metrics
from ..core import policy
from ..core.recovery import Action, CircuitBreaker, Controls, RecoveryManager, RunState, backoff_delay, classify
from ..core.states import OUTBOX_TRANSITIONS, PRE_SIDE_EFFECT_STATES, JobState, OutboxState, check
from ..intel.profile import Profile
from ..intel.reasoning import Drafter
from ..platform.base import PlatformAdapter

log = logging.getLogger("engine")
J = JobState


class Engine:
    def __init__(self, settings: Settings, db: Database, platform: PlatformAdapter, drafter: Drafter, profile: Profile):
        self.s = settings
        self.db = db
        self.platform = platform
        self.drafter = drafter
        self.profile = profile
        self.jobs = JobQueue(db, settings.limits)
        self.controls = Controls(db)
        self.circuit = CircuitBreaker(db, settings.circuit)
        self.recovery = RecoveryManager(db, settings.limits, self.circuit, self.controls)
        self.owner = f"worker-{uuid.uuid4().hex[:8]}"
        self.max_reconcile_checks = 3

    # ------------------------------------------------------------------ lifecycle
    def startup_recovery(self) -> dict:
        """Reconcile the durable state left by a previous run. Never re-executes a recorded action blindly."""
        summary = {"requeued": 0, "delivery_unknown": 0, "attempts_marked_unknown": 0}
        with self.db.transaction():
            for r in self.db.all("SELECT id, state FROM jobs"):
                state = JobState(r["state"])
                if state in PRE_SIDE_EFFECT_STATES:
                    self.jobs.transition(r["id"], J.QUEUED, "restart: interrupted before any side effect")
                    summary["requeued"] += 1
                elif state in (J.SENDING, J.VERIFYING):
                    self.jobs.transition(r["id"], J.DELIVERY_UNKNOWN, "restart: interrupted during send; reconcile before any resend")
                    summary["delivery_unknown"] += 1
            for r in self.db.all("SELECT id FROM outbox WHERE state = 'attempting'"):
                self._outbox_state(r["id"], OutboxState.UNKNOWN)
                self.db.execute("UPDATE delivery_attempts SET result = 'unknown', finished_at = ?, error = ? "
                                "WHERE outbox_id = ? AND result IS NULL", (iso(), "process stopped mid-attempt", r["id"]))
                summary["attempts_marked_unknown"] += 1
            self.db.audit("system", "startup_recovery", summary)
        log.info("startup recovery", extra={"outcome": json.dumps(summary)})
        return summary

    def run_once(self, max_jobs: int = 10) -> dict:
        """One scheduler tick. Returns counters for observability and tests."""
        out = {"ingested_followers": 0, "ingested_messages": 0, "processed": 0, "reconciled": 0}
        self.jobs.release_due_retries()
        self.recover_stale()
        self.release_due_blocked()
        # Retention is local housekeeping: it runs even while paused or with the circuit open.
        last = parse(self.db.get_state("retention_ran_at"))
        if last is None or (now() - last).total_seconds() > 86_400:
            out["retention_deleted"] = self.apply_retention()
            self.db.set_state("retention_ran_at", iso())
        if self.controls.emergency_stopped or self.controls.run_state is not RunState.RUNNING:
            return out
        if not self.circuit.allow():
            return out
        out["reconciled"] = self.reconcile_unknowns()
        self.reconcile_backlog()
        try:
            out["ingested_followers"] = self.ingest_followers()
            out["ingested_messages"] = self.ingest_conversations()
        except Exception as e:  # ingestion failure is platform-scoped
            self._platform_failure(e, "ingestion")
            return out
        if self.s.mode is Mode.OBSERVE:
            return out
        for _ in range(max_jobs):
            if self.controls.run_state is not RunState.RUNNING or not self.circuit.allow():
                break
            # Prepare only a few hellos ahead of your approval; approved ones and replies always go ahead.
            waiting = self.db.scalar("SELECT COUNT(*) FROM jobs WHERE kind = 'intro' AND state IN ('awaiting_approval', 'needs_review')")
            job = self.jobs.claim(self.owner, hold_new=["intro"] if waiting >= self.s.limits.max_waiting_intros else None)
            if not job:
                break
            self.process(job)
            out["processed"] += 1
        return out

    # ------------------------------------------------------------------ ingestion
    def ingest_followers(self) -> int:
        caps = self.platform.capabilities()
        if caps.follower_events == "unavailable":
            return 0
        n = 0
        last = parse(self.db.get_state("list_checked_at"))
        if last is not None and (now() - last).total_seconds() < self.s.list_check_interval_s:
            return 0
        observations = self.platform.poll_followers()
        self.db.set_state("list_checked_at", iso())
        if not observations:
            return 0                         # never take an empty read as the baseline: everyone would look new later
        # "following": everyone you follow is someone to say hello to, so there's no baseline. "followers": the
        # first read is a baseline (people who already followed you), unless intro_existing_followers.
        by_followers = self.s.policy.audience == "followers"
        baseline = (by_followers and caps.follower_events == "inferred" and not self.db.get_state("followers_baselined", False)
                    and not self.s.policy.intro_existing_followers)
        # An inferred list is newest first, so new followers come before everyone already known. Unknown names
        # further down are older followers that scrolled into view (e.g. after an unfollow), not new ones.
        past_known = False
        for obs in observations:
            dedup = f"event:{obs.event_id}" if obs.event_id else f"{obs.source}:{obs.username}"
            with self.db.transaction():
                cur = self.db.execute("INSERT OR IGNORE INTO follower_events(id, dedup_key, username, source, observed_at, payload) "
                                      "VALUES (?, ?, ?, ?, ?, ?)", (new_id("fev"), dedup, obs.username, obs.source, iso(),
                                                                     json.dumps({"display_name": obs.display_name})))
                if cur.rowcount == 0:
                    metrics.inc("duplicate_events_prevented")
                    past_known = True
                    continue
                acct = self._upsert_account(obs.username, obs.display_name, obs.platform_id, follower_source=obs.source)
                n += 1
                if baseline or (by_followers and past_known and obs.source == "inferred"):
                    self.db.execute("UPDATE accounts SET intro_status = CASE WHEN intro_status = 'none' THEN 'skipped' ELSE intro_status END, "
                                    "review_reason = COALESCE(review_reason, ?) WHERE id = ?",
                                    ("already a follower at first run" if baseline else "older follower, seen below known ones", acct["id"]))
                    continue
                if not self.s.policy.intros_enabled:
                    continue
                ok, why = policy.intro_eligibility(self.s, username=acct["username"], intro_status=acct["intro_status"],
                                                   opted_out=bool(acct["opted_out"]), review_status=acct["review_status"])
                if not ok:
                    self.db.audit("system", "intro_not_eligible", {"account": acct["id"], "reason": why})
                    continue
                try:
                    if self.jobs.enqueue("intro", obs.username, f"intro:{acct['id']}", {"account_id": acct["id"]}) is None:
                        metrics.inc("duplicate_events_prevented")
                except QueueFull as e:
                    # Backpressure: the follower event is stored, the account stays eligible, and a later
                    # reconcile_backlog() pass enqueues it once the queue drains.
                    metrics.inc("queue_full")
                    self.db.execute("INSERT INTO recovery_events(at, scope, subject, category, decision, detail) VALUES (?, 'application', ?, 'transient', 'backpressure', ?)",
                                    (iso(), obs.username, json.dumps({"reason": str(e)})))
        if baseline:
            with self.db.transaction():
                self.db.set_state("followers_baselined", True)
                self.db.audit("system", "followers_baselined", {"count": len(observations)})
        return n

    def import_intro_list(self, usernames: list[str], actor: str = "operator") -> dict:
        """Queue introductions for a list of usernames you supply (e.g. your followers list). Each one still goes
        through intro_eligibility, the never-list, the age checks, the daily limit and, on a real platform, your
        approval. Accounts are marked follower_source='imported' (not observed on the platform)."""
        valid = re.compile(r"^[a-z0-9._]{1,30}$")
        out = {"queued": 0, "already_known": 0, "not_eligible": 0, "invalid": 0}
        for raw in usernames:
            u = raw.strip().lstrip("@").lower()
            if not u:
                continue
            if not valid.match(u):
                out["invalid"] += 1
                continue
            with self.db.transaction():
                acct = self.db.one("SELECT * FROM accounts WHERE username = ?", (u,))
                if acct is None:
                    ts = iso()
                    aid = new_id("acc")
                    self.db.execute("INSERT INTO accounts(id, username, first_seen_at, last_seen_at, follower_status, follower_source, "
                                    "created_at, updated_at) VALUES (?, ?, ?, ?, 'unknown', 'imported', ?, ?)", (aid, u, ts, ts, ts, ts))
                    acct = self.db.one("SELECT * FROM accounts WHERE id = ?", (aid,))
                ok, why = policy.intro_eligibility(self.s, username=u, intro_status=acct["intro_status"],
                                                   opted_out=bool(acct["opted_out"]), review_status=acct["review_status"])
                if not ok:
                    out["not_eligible"] += 1
                    self.db.audit("system", "intro_not_eligible", {"account": acct["id"], "reason": why})
                    continue
                if self.jobs.enqueue("intro", u, f"intro:{acct['id']}", {"account_id": acct["id"]}):
                    out["queued"] += 1
                else:
                    out["already_known"] += 1
        self.db.audit(actor, "import_intro_list", out)
        return out

    def reconcile_backlog(self) -> int:
        """Enqueue intros for eligible followers that have no job yet (e.g. dropped by backpressure)."""
        n = 0
        for acct in self.db.all("SELECT a.* FROM accounts a WHERE follower_status = 'follower' AND intro_status = 'none' "
                                "AND NOT EXISTS (SELECT 1 FROM jobs j WHERE j.dedup_key = 'intro:' || a.id)"):
            ok, _ = policy.intro_eligibility(self.s, username=acct["username"], intro_status=acct["intro_status"],
                                             opted_out=bool(acct["opted_out"]), review_status=acct["review_status"])
            if not ok:
                continue
            try:
                if self.jobs.enqueue("intro", acct["username"], f"intro:{acct['id']}", {"account_id": acct["id"]}):
                    n += 1
            except QueueFull:
                break
        return n

    def _upsert_account(self, username: str, display_name: str | None, platform_id: str | None, follower_source: str):
        ts = iso()
        by_pid = self.db.one("SELECT * FROM accounts WHERE platform_id = ?", (platform_id,)) if platform_id else None
        by_name = self.db.one("SELECT * FROM accounts WHERE username = ?", (username,))
        if by_pid and by_name and by_pid["id"] != by_name["id"]:
            raise DataIntegrityError(f"conflicting identities for {username}: platform id and username map to different accounts")
        if by_name and platform_id and by_name["platform_id"] and by_name["platform_id"] != platform_id:
            raise DataIntegrityError(f"username {username} now has a different platform id")
        row = by_pid or by_name
        if row:
            if row["username"] != username:
                self.db.audit("system", "username_changed", {"account": row["id"], "from": row["username"], "to": username})
            self.db.execute("UPDATE accounts SET username = ?, display_name = COALESCE(?, display_name), platform_id = COALESCE(platform_id, ?), "
                            "follower_status = 'follower', follower_source = ?, last_seen_at = ?, updated_at = ? WHERE id = ?",
                            (username, display_name, platform_id, follower_source, ts, ts, row["id"]))
            return self.db.one("SELECT * FROM accounts WHERE id = ?", (row["id"],))
        aid = new_id("acc")
        self.db.execute("INSERT INTO accounts(id, platform_id, username, display_name, first_seen_at, last_seen_at, follower_status, "
                        "follower_source, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'follower', ?, ?, ?)",
                        (aid, platform_id, username, display_name, ts, ts, follower_source, ts, ts))
        return self.db.one("SELECT * FROM accounts WHERE id = ?", (aid,))

    def _ensure_account(self, username: str):
        row = self.db.one("SELECT * FROM accounts WHERE username = ?", (username,))
        if row:
            return row
        ts = iso()
        aid = new_id("acc")
        self.db.execute("INSERT INTO accounts(id, username, first_seen_at, last_seen_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (aid, username, ts, ts, ts, ts))
        return self.db.one("SELECT * FROM accounts WHERE id = ?", (aid,))

    def _sync_thread(self, snap) -> str:
        """Upsert a conversation and its messages. Returns the conversation id."""
        ts = iso()
        conv = self.db.one("SELECT * FROM conversations WHERE thread_key = ?", (snap.thread_key,))
        acct = self._ensure_account(snap.username)
        if conv and conv["account_id"] != acct["id"]:
            raise DataIntegrityError(f"thread {snap.thread_key} changed owner")
        if not conv:
            cid = new_id("conv")
            self.db.execute("INSERT INTO conversations(id, thread_key, account_id, identity_source, created_at, updated_at) "
                            "VALUES (?, ?, ?, ?, ?, ?)", (cid, snap.thread_key, acct["id"], snap.identity_source, ts, ts))
        else:
            cid = conv["id"]
        for m in snap.messages:
            self.db.execute("INSERT OR IGNORE INTO messages(id, conversation_id, platform_message_id, id_source, direction, text, sent_at, observed_at) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)", (new_id("msg"), cid, m.platform_message_id, m.id_source, m.direction, m.text, m.sent_at, ts))
        return cid

    def ingest_conversations(self) -> int:
        if not self.platform.capabilities().read_threads:
            return 0
        n = 0
        for snap in self.platform.list_threads():
            with self.db.transaction():
                cid = self._sync_thread(snap)
                conv = self.db.one("SELECT * FROM conversations WHERE id = ?", (cid,))
                if not snap.messages:
                    continue
                last = snap.messages[-1]
                if last.direction != "in" or conv["status"] != "active" or not self.s.policy.replies_enabled:
                    continue
                if policy.is_excluded(self.s, snap.username):
                    continue
                if conv["last_processed_msg_id"] == last.platform_message_id:
                    continue
                if self.jobs.enqueue("reply", snap.username, f"reply:{snap.thread_key}:{last.platform_message_id}",
                                     {"thread_key": snap.thread_key, "message_id": last.platform_message_id}):
                    n += 1
        return n

    # ------------------------------------------------------------------ job processing
    def process(self, job: Job) -> None:
        started = time.monotonic()
        try:
            if job.kind == "intro":
                self._handle_intro(job)
            elif job.kind == "reply":
                self._handle_reply(job)
            else:
                raise DataIntegrityError(f"unknown job kind {job.kind}")
            self.circuit.record_success()
            self._reset_account_failures(job)
        except Exception as e:
            self.handle_failure(job, e)
        finally:
            metrics.inc("jobs_processed")
            log.info("job processed", extra={"job_id": job.id, "duration_ms": int((time.monotonic() - started) * 1000),
                                             "outcome": self.db.scalar("SELECT state FROM jobs WHERE id = ?", (job.id,))})

    def _step(self, job: Job, state: JobState, reason: str, **fields) -> None:
        self.jobs.transition(job.id, state, reason, owner=self.owner, **fields)

    def _verify_identity(self) -> None:
        me = self.platform.whoami()
        if me.lower() != self.s.account_username.lower():
            raise AuthenticationRequired(f"logged in as {me!r}, expected {self.s.account_username!r}")

    def _existing_outbox(self, job_id: str):
        return self.db.one("SELECT * FROM outbox WHERE job_id = ?", (job_id,))

    def _handle_intro(self, job: Job) -> None:
        acct = self.db.one("SELECT * FROM accounts WHERE id = ?", (job.payload["account_id"],))
        if (ob := self._existing_outbox(job.id)) is not None:
            return self._resume_send(job, ob)
        self._step(job, J.INSPECTING, "verifying account and conversation")
        self.controls.guard("inspect")
        self._verify_identity()
        ok, why = policy.intro_eligibility(self.s, username=acct["username"], intro_status=acct["intro_status"],
                                           opted_out=bool(acct["opted_out"]), review_status=acct["review_status"])
        if not ok:
            return self._step(job, J.SKIPPED, f"not eligible: {why}")
        snap = self.platform.open_dm(acct["username"])
        if snap.username.lower() != acct["username"].lower():
            raise AmbiguousState(f"opened conversation belongs to {snap.username!r}, not {acct['username']!r}")
        with self.db.transaction():
            self._sync_thread(snap)
        if snap.messages:
            # Prior contact exists. Never send an intro into an existing conversation.
            with self.db.transaction():
                self.db.execute("UPDATE accounts SET intro_status = 'skipped', updated_at = ? WHERE id = ?", (iso(), acct["id"]))
                self._step(job, J.SKIPPED, "conversation already has messages; no introduction needed")
            return
        self.jobs.set_payload(job.id, thread_key=snap.thread_key)
        self._step(job, J.REASONING, "choosing introduction")
        recent = [r["text"] for r in self.db.all("SELECT text FROM outbox ORDER BY created_at DESC LIMIT 5")]
        seed = int(hashlib.sha256(acct["username"].encode()).hexdigest(), 16)
        outcome = self.drafter.draft_intro(acct["username"], recent, seed)
        self._decide_delivery(job, target=acct["username"], thread_key=snap.thread_key, outcome=outcome, context={"kind": "intro"},
                              account_id=acct["id"])

    def _handle_reply(self, job: Job) -> None:
        thread_key = job.payload["thread_key"]
        if (ob := self._existing_outbox(job.id)) is not None:
            return self._resume_send(job, ob)
        self._step(job, J.INSPECTING, "reading conversation")
        self.controls.guard("inspect")
        self._verify_identity()
        conv = self.db.one("SELECT * FROM conversations WHERE thread_key = ?", (thread_key,))
        acct = self.db.one("SELECT * FROM accounts WHERE id = ?", (conv["account_id"],))
        if policy.is_excluded(self.s, acct["username"]):
            return self._step(job, J.SKIPPED, "on the never-message list")
        snap = self.platform.read_thread(thread_key)
        if snap.username.lower() != acct["username"].lower():
            raise AmbiguousState(f"thread {thread_key} shows {snap.username!r}, expected {acct['username']!r}")
        if not snap.messages:
            # An inaccessible or empty-looking thread is not proof of an empty conversation.
            raise AmbiguousState("conversation shows no messages; refusing to treat it as empty")
        with self.db.transaction():
            self._sync_thread(snap)
        target_id = job.payload["message_id"]
        idx = next((i for i, m in enumerate(snap.messages) if m.platform_message_id == target_id), None)
        if idx is None:
            raise AmbiguousState(f"message {target_id} no longer visible in the thread")
        later = snap.messages[idx + 1:]
        if any(m.direction == "out" for m in later):
            return self._finish_without_reply(job, conv, target_id, "already answered")
        if any(m.direction == "in" for m in later):
            return self._finish_without_reply(job, conv, target_id, "superseded by a newer message")
        incoming = snap.messages[idx].text
        if policy.wants_to_disengage(incoming):
            with self.db.transaction():
                self.db.execute("UPDATE conversations SET status = 'opted_out', updated_at = ? WHERE id = ?", (iso(), conv["id"]))
                self.db.execute("UPDATE accounts SET opted_out = 1, updated_at = ? WHERE id = ?", (iso(), acct["id"]))
                self._journal(acct["username"], "They asked not to be contacted. Conversation closed.", "observed", None)
                self._finish_without_reply(job, conv, target_id, "they asked to stop; no reply and no further contact")
            return
        if policy.age_blocks_contact(self.s, incoming):
            with self.db.transaction():
                self.db.execute("UPDATE conversations SET status = 'needs_review', updated_at = ? WHERE id = ?", (iso(), conv["id"]))
                self.db.execute("UPDATE accounts SET opted_out = 1, review_status = 'needs_review', review_reason = ?, updated_at = ? WHERE id = ?",
                                ("stated age outside the allowed range", iso(), acct["id"]))
                self._finish_without_reply(job, conv, target_id, "stated age outside the allowed range; contact stopped")
            return
        self._step(job, J.REASONING, "drafting reply")
        self.controls.guard("draft")
        history = [(r["direction"], r["text"]) for r in self.db.all(
            "SELECT direction, text FROM messages WHERE conversation_id = ? ORDER BY rowid", (conv["id"],))]
        memory = [f"{r['kind']}: {r['content']}" for r in self.db.all(
            "SELECT kind, content FROM memory WHERE target = ? ORDER BY created_at", (acct["username"],))]
        outcome = self.drafter.draft_reply(history, memory)
        self.jobs.heartbeat(job.id, self.owner)
        with self.db.transaction():
            self._remember(acct["username"], "their_statement", incoming, f"message:{target_id}", 1.0)
            for note in outcome.memory_notes[:5]:
                if note.get("kind") in ("interpretation", "open_question"):
                    self._remember(acct["username"], note["kind"], str(note.get("content", ""))[:300], "model", note.get("confidence"))
        self._decide_delivery(job, target=acct["username"], thread_key=thread_key, outcome=outcome,
                              context={"kind": "reply", "replying_to": target_id, "history_tail": history[-6:], **outcome.plain},
                              account_id=acct["id"], conv_id=conv["id"], message_id=target_id)

    def _finish_without_reply(self, job: Job, conv, message_id: str, reason: str) -> None:
        with self.db.transaction():
            self.db.execute("UPDATE conversations SET last_processed_msg_id = ?, updated_at = ? WHERE id = ?", (message_id, iso(), conv["id"]))
            self._step(job, J.COMPLETED, reason)

    def _decide_delivery(self, job: Job, *, target: str, thread_key: str, outcome, context: dict, account_id: str,
                         conv_id: str | None = None, message_id: str | None = None) -> None:
        draft_id = new_id("drf")
        perm = policy.can_send(self.s, self.platform.is_real)
        with self.db.transaction():
            # Only the newest draft of a job can ever be approved.
            self.db.execute("UPDATE drafts SET status = 'superseded' WHERE job_id = ? AND status = 'pending'", (job.id,))
            self.db.execute(
                "INSERT INTO drafts(id, job_id, target, text, intent, rationale, facts_used, context, validation, status, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)",
                (draft_id, job.id, target, outcome.text, outcome.intent, outcome.reason,
                 json.dumps([f.model_dump() for f in outcome.facts_used]), json.dumps(context, default=str),
                 json.dumps(outcome.validation), iso()))
            self.jobs.set_payload(job.id, draft_id=draft_id)
            if outcome.action == "escalate":
                self._step(job, J.NEEDS_REVIEW, f"draft needs a human: {outcome.reason or 'flagged by validation'}")
                return
            if job.kind == "intro":
                self.db.execute("UPDATE accounts SET intro_status = 'pending', updated_at = ? WHERE id = ?", (iso(), account_id))
            auto = perm.allowed and not perm.requires_approval
            self._create_outbox(job, draft_id, target, outcome.text, approved_by="policy:mock" if auto else None)
            if not auto:
                self._step(job, J.AWAITING_APPROVAL, "awaiting operator approval" if perm.allowed else perm.reason)
                return
            self.db.execute("UPDATE drafts SET status = 'approved', decided_by = 'policy:mock', decided_at = ? WHERE id = ?", (iso(), draft_id))
            self._step(job, J.READY_TO_SEND, "auto-approved by mock-platform policy")
        self._send(job, thread_key)

    @staticmethod
    def approval_hash(target: str, text: str) -> str:
        """Binds an approval to one recipient and one exact text."""
        return hashlib.sha256(f"{target.lower()}\x00{text}".encode()).hexdigest()

    def _create_outbox(self, job: Job, draft_id: str, target: str, text: str, approved_by: str | None) -> str:
        op = new_id("op")
        ts = iso()
        self.db.execute("INSERT INTO outbox(id, job_id, draft_id, target, text, state, approved_by, approved_at, approved_hash, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?)",
                        (op, job.id, draft_id, target, text, approved_by, ts if approved_by else None,
                         self.approval_hash(target, text) if approved_by else None, ts, ts))
        return op

    def _cancel_outbox(self, job: Job, ob, reason: str, intro_status: str | None = None) -> None:
        """The approval no longer applies: cancel the unsent message and close the job with the reason."""
        with self.db.transaction():
            if ob["state"] == OutboxState.PENDING.value:
                self._outbox_state(ob["id"], OutboxState.CANCELLED)
            self.db.execute("UPDATE drafts SET status = 'superseded' WHERE id = ? AND status IN ('pending', 'approved')", (ob["draft_id"],))
            if intro_status:
                self._mark_intro(job, intro_status)
            self.db.audit("system", "approval_invalidated", {"job": job.id, "outbox": ob["id"], "reason": reason})
            self._step(job, J.SKIPPED, reason)

    def _approval_still_valid(self, job: Job, ob, snap) -> bool:
        """Re-validate an approval against the conversation as it is now. Returns False after closing the job."""
        if ob["approved_by"] == "operator":
            approved_at = parse(ob["approved_at"])
            if approved_at is None or (now() - approved_at).total_seconds() > self.s.policy.approval_ttl_hours * 3600:
                with self.db.transaction():
                    self.db.execute("UPDATE outbox SET approved_by = NULL, approved_at = NULL, approved_hash = NULL, updated_at = ? WHERE id = ?",
                                    (iso(), ob["id"]))
                    self.db.execute("UPDATE drafts SET status = 'pending' WHERE id = ?", (ob["draft_id"],))
                    self._step(job, J.NEEDS_REVIEW, f"approval older than {self.s.policy.approval_ttl_hours:g} h; approve again if still wanted")
                return False
        acct = self.db.one("SELECT * FROM accounts WHERE username = ?", (ob["target"],))
        if policy.is_excluded(self.s, ob["target"]):
            self._cancel_outbox(job, ob, "added to the never-message list after approval", intro_status="skipped")
            return False
        if acct and (acct["opted_out"] or acct["review_status"] != "ok"):
            self._cancel_outbox(job, ob, "the person opted out or is under review since this was approved")
            return False
        if job.kind == "intro":
            if snap.messages:
                self._cancel_outbox(job, ob, "a conversation exists now; an introduction is no longer appropriate", intro_status="skipped")
                return False
            return True
        target_id = job.payload["message_id"]
        idx = next((i for i, m in enumerate(snap.messages) if m.platform_message_id == target_id), None)
        if idx is None:
            raise AmbiguousState(f"message {target_id} is no longer visible; can't confirm the approval still applies")
        later = snap.messages[idx + 1:]
        if any(m.direction == "in" and policy.wants_to_disengage(m.text) for m in later):
            self._cancel_outbox(job, ob, "they asked to stop after this reply was approved")
            return False
        if later:
            self._cancel_outbox(job, ob, "the conversation moved on after approval (newer messages); this reply is superseded")
            return False
        return True

    def _resume_send(self, job: Job, ob) -> None:
        """A job that already has an outbox row resumes from the outbox state, never from scratch."""
        state = OutboxState(ob["state"])
        if state is OutboxState.SENT:
            self._step(job, J.INSPECTING, "outbox already sent")
            self._step(job, J.COMPLETED, "already delivered")
            return
        if state in (OutboxState.UNKNOWN, OutboxState.ATTEMPTING):
            raise DeliveryUnknown("an earlier attempt has an unknown outcome; reconcile first")
        if state is OutboxState.CANCELLED:
            self._step(job, J.SKIPPED, "message was cancelled")
            return
        if not ob["approved_by"]:
            raise DataIntegrityError("job is runnable but its outbox row was never approved")
        thread_key = json.loads(self.db.scalar("SELECT payload FROM jobs WHERE id = ?", (job.id,))).get("thread_key")
        self._step(job, J.INSPECTING, "re-verifying before send")
        self._verify_identity()
        snap = self.platform.read_thread(thread_key)
        if snap.username.lower() != ob["target"].lower():
            raise AmbiguousState(f"thread {thread_key} shows {snap.username!r}, expected {ob['target']!r}")
        with self.db.transaction():
            self._sync_thread(snap)
        if not self._approval_still_valid(job, ob, snap):
            return
        if state is OutboxState.FAILED:
            with self.db.transaction():
                self._outbox_state(ob["id"], OutboxState.PENDING)
        self._step(job, J.READY_TO_SEND, "verified; sending")
        self._send(job, thread_key)

    def _send(self, job: Job, thread_key: str) -> None:
        perm = policy.can_send(self.s, self.platform.is_real)
        if not perm.allowed:
            # Park it with the reason instead of failing: retrying every tick would only repeat the same refusal.
            self._step(job, J.BLOCKED, f"{perm.reason}; switch mode and press Retry to send")
            return
        sent_today = self.db.scalar("SELECT COUNT(*) FROM outbox WHERE state = 'sent' AND updated_at >= ?",
                                    (iso(now() - timedelta(days=1)),))
        if sent_today >= self.s.limits.daily_send_limit:
            self._step(job, J.BLOCKED, f"daily send limit ({self.s.limits.daily_send_limit}) reached",
                       run_after=iso(now() + timedelta(hours=6)))
            return
        # Commit the attempt BEFORE touching the platform. Every check that gates the send runs inside the same
        # transaction, on the row as committed, so nothing can change between the check and the attempt.
        with self.db.transaction():
            self.controls.guard("send")
            ob = self._existing_outbox(job.id)
            if ob["state"] != OutboxState.PENDING.value:
                raise DataIntegrityError(f"outbox {ob['id']} is {ob['state']}, expected pending")
            policy.assert_deliverable(platform_is_real=self.platform.is_real, approved_by=ob["approved_by"])
            if ob["approved_hash"] != self.approval_hash(ob["target"], ob["text"]):
                raise DataIntegrityError(f"outbox {ob['id']}: text or recipient differs from what was approved")
            if ob["target"].lower() != job.subject_key.lower():
                raise DataIntegrityError(f"outbox {ob['id']} is addressed to {ob['target']!r}, job is for {job.subject_key!r}")
            self._outbox_state(ob["id"], OutboxState.ATTEMPTING)
            attempt_id = self.db.execute("INSERT INTO delivery_attempts(outbox_id, started_at) VALUES (?, ?)", (ob["id"], iso())).lastrowid
            self._step(job, J.SENDING, f"sending operation {ob['id']}")
        try:
            result = self.platform.send(thread_key, ob["text"], ob["id"])
        except Exception as e:
            # Anything after the click may have delivered. Never retry blindly: mark unknown and reconcile.
            self._record_outcome(job, ob, attempt_id, "unknown", f"{type(e).__name__}: {e}"[:300], None)
            metrics.inc("delivery_unknown")
            self.recovery.record(self.recovery.decide(exc=DeliveryUnknown(str(e)), subject=job.subject_key, attempts=job.attempts,
                                                      category_count=0, account_failures=0), scope="job", job_id=job.id, subject=job.subject_key)
            if isinstance(e, BrowserCrashed):
                log.warning("browser crashed during send", extra={"job_id": job.id})
            self.reconcile_one(ob["id"])
            return
        self._record_outcome(job, ob, attempt_id, result.status, result.detail or None, result.platform_message_id)

    def _record_outcome(self, job: Job, ob, attempt_id: int, status: str, detail: str | None, platform_message_id: str | None) -> None:
        """Record what the platform told us. Tolerates the job having been taken over meanwhile (a send that
        outlived its lease is moved to delivery_unknown by the watchdog): the observed outcome still wins."""
        with self.db.transaction():
            self.db.execute("UPDATE delivery_attempts SET finished_at = ?, result = ?, error = ? WHERE id = ?",
                            (iso(), status, detail, attempt_id))
            current_ob = OutboxState(self.db.scalar("SELECT state FROM outbox WHERE id = ?", (ob["id"],)))
            current_job = self.jobs.get(job.id).state
            took_over = current_job is not J.SENDING
            if status == "sent":
                self._outbox_state(ob["id"], OutboxState.SENT, platform_message_id=platform_message_id)
                if not took_over:
                    self._step(job, J.VERIFYING, "platform confirmed send")
            elif status == "failed":
                self._outbox_state(ob["id"], OutboxState.FAILED)
                self.jobs.transition(job.id, J.RETRY_SCHEDULED, f"platform rejected send: {detail}",
                                     run_after=iso(now() + timedelta(seconds=backoff_delay(job.attempts, self.s.limits))))
                return
            else:
                if current_ob is not OutboxState.UNKNOWN:
                    self._outbox_state(ob["id"], OutboxState.UNKNOWN)
                if not took_over:
                    self._step(job, J.DELIVERY_UNKNOWN, "platform could not confirm the send")
                self._mark_intro(job, "unknown")
        if status == "unknown":
            self.reconcile_one(ob["id"])
            return
        self._complete_sent(job.id, ob["id"])

    def _complete_sent(self, job_id: str, outbox_id: str) -> None:
        job = self.jobs.get(job_id)
        ob = self.db.one("SELECT * FROM outbox WHERE id = ?", (outbox_id,))
        with self.db.transaction():
            self.jobs.transition(job_id, J.COMPLETED, "delivered")
            self._mark_intro(job, "sent")
            if job.kind == "reply":
                conv = self.db.one("SELECT * FROM conversations WHERE thread_key = ?", (job.payload["thread_key"],))
                self.db.execute("UPDATE conversations SET last_processed_msg_id = ?, updated_at = ? WHERE id = ?",
                                (job.payload["message_id"], iso(), conv["id"]))
            self._journal(ob["target"], f"Sent: {ob['text']}", "observed", None)
        metrics.inc("messages_sent")

    def _mark_intro(self, job: Job, status: str) -> None:
        if job.kind == "intro":
            self.db.execute("UPDATE accounts SET intro_status = ?, updated_at = ? WHERE id = ?", (status, iso(), job.payload["account_id"]))

    def _outbox_state(self, outbox_id: str, target: OutboxState, **fields) -> None:
        cur = OutboxState(self.db.scalar("SELECT state FROM outbox WHERE id = ?", (outbox_id,)))
        check(OUTBOX_TRANSITIONS, cur, target)
        sets = {"state": target.value, "updated_at": iso(), **fields}
        self.db.execute(f"UPDATE outbox SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?", (*sets.values(), outbox_id))

    # ------------------------------------------------------------------ reconciliation
    def reconcile_unknowns(self) -> int:
        n = 0
        for r in self.db.all("SELECT id FROM outbox WHERE state = 'unknown'"):
            if self.reconcile_one(r["id"]):
                n += 1
        return n

    def reconcile_one(self, outbox_id: str) -> bool:
        """Returns True if the outcome was established. Never sends anything."""
        ob = self.db.one("SELECT * FROM outbox WHERE id = ?", (outbox_id,))
        job = self.jobs.get(ob["job_id"])
        if job.state is not J.DELIVERY_UNKNOWN:
            return False
        caps = self.platform.capabilities()
        since = self.db.scalar("SELECT MIN(started_at) FROM delivery_attempts WHERE outbox_id = ?", (outbox_id,))
        try:
            verdict = self.platform.find_sent(job.payload.get("thread_key"), ob["text"], since) if caps.delivery_check != "unavailable" else "unknown"
        except Exception as e:
            log.warning("reconciliation check failed", extra={"job_id": job.id, "category": classify(e).value})
            verdict = "unknown"
        checks = job.payload.get("reconcile_checks", 0) + 1
        self.jobs.set_payload(job.id, reconcile_checks=checks)
        if verdict == "found":
            with self.db.transaction():
                self._outbox_state(outbox_id, OutboxState.SENT)
                self.db.execute("UPDATE delivery_attempts SET result = 'sent', error = COALESCE(error, '') || ' (reconciled: found)' "
                                "WHERE outbox_id = ? AND result = 'unknown'", (outbox_id,))
            self._complete_sent(job.id, outbox_id)
            self.recovery.record(_info("reconciled: message found, marked sent"), scope="job", job_id=job.id, subject=job.subject_key)
            return True
        if verdict == "absent" and caps.delivery_check == "authoritative":
            with self.db.transaction():
                self._outbox_state(outbox_id, OutboxState.FAILED)
                delay = backoff_delay(job.attempts, self.s.limits)
                self.jobs.transition(job.id, J.RETRY_SCHEDULED, "authoritative check: not delivered; safe to retry",
                                     run_after=iso(now() + timedelta(seconds=delay)))
                self._mark_intro(job, "pending")
            self.recovery.record(_info("reconciled: confirmed not delivered"), scope="job", job_id=job.id, subject=job.subject_key)
            return True
        if checks >= self.max_reconcile_checks:
            with self.db.transaction():
                self.jobs.transition(job.id, J.NEEDS_REVIEW, "delivery could not be established; operator must decide (no automatic resend)")
            self.recovery.record(_info("escalated: delivery unknown after checks", ErrorCategory.DELIVERY_UNKNOWN),
                                 scope="job", job_id=job.id, subject=job.subject_key)
        return False

    # ------------------------------------------------------------------ failures
    def handle_failure(self, job: Job, exc: BaseException) -> None:
        if isinstance(exc, LeaseLost):
            # Someone else (watchdog / restart recovery) owns this job now. Touch nothing; just leave a record.
            self.db.execute("INSERT INTO recovery_events(at, scope, job_id, subject, category, decision, detail) VALUES (?, 'job', ?, ?, 'transient', 'abandon_lease_lost', ?)",
                            (iso(), job.id, job.subject_key, json.dumps({"reason": str(exc)[:300]})))
            metrics.inc("lease_lost")
            return
        current = self.jobs.get(job.id)
        cat = classify(exc)
        count = self.jobs.bump_category(job.id, cat.value)
        acct = self.db.one("SELECT * FROM accounts WHERE username = ?", (job.subject_key,))
        acct_failures = acct["consecutive_failures"] if acct else 0
        decision = self.recovery.decide(exc=exc, subject=job.subject_key, attempts=current.attempts, category_count=count,
                                        account_failures=acct_failures)
        metrics.inc(f"errors.{cat.value}")
        log.warning("job failed", extra={"job_id": job.id, "account": job.subject_key, "category": cat.value,
                                         "decision": decision.action.value, "retry": current.attempts})
        try:
            with self.db.transaction():
                self.recovery.record(decision, scope="job", job_id=job.id, subject=job.subject_key)
                err = f"{type(exc).__name__}: {exc}"[:500]
                self.db.execute("UPDATE jobs SET last_error = ?, error_category = ? WHERE id = ?", (err, cat.value, job.id))
                if acct:
                    self.db.execute("UPDATE accounts SET consecutive_failures = consecutive_failures + 1 WHERE id = ?", (acct["id"],))
                state = current.state
                side_effect_free = state in PRE_SIDE_EFFECT_STATES or state is J.READY_TO_SEND
                a = decision.action
                if a is Action.RETRY and side_effect_free:
                    self.jobs.transition(job.id, J.RETRY_SCHEDULED, f"{cat.value}: retry in {decision.delay_s:.0f}s",
                                         run_after=iso(now() + timedelta(seconds=decision.delay_s)))
                elif a is Action.SKIP_ACCOUNT and side_effect_free:
                    target = J.SKIPPED if cat in (ErrorCategory.PERMISSION, ErrorCategory.PERMANENT) else J.NEEDS_REVIEW
                    self.jobs.transition(job.id, target, decision.reason)
                    if acct:
                        self.db.execute("UPDATE accounts SET review_status = ?, review_reason = ?, intro_status = CASE WHEN intro_status = 'none' "
                                        "AND ? = 'permission' THEN 'skipped' ELSE intro_status END WHERE id = ?",
                                        ("ok" if target is J.SKIPPED else "needs_review", decision.reason[:300], cat.value, acct["id"]))
                elif a in (Action.PAUSE_APP, Action.STOP_APP) and side_effect_free:
                    self.jobs.transition(job.id, J.QUEUED, f"held: {decision.reason}")
                    if a is Action.PAUSE_APP:
                        self.controls.pause(decision.reason)
                elif a is Action.OPEN_CIRCUIT and side_effect_free:
                    if cat is ErrorCategory.RATE_LIMITED:
                        self.circuit.force_open(decision.reason)
                    self.jobs.transition(job.id, J.RETRY_SCHEDULED, decision.reason,
                                         run_after=iso(now() + timedelta(seconds=self.s.circuit.cooldown_s)))
                elif a is Action.QUARANTINE_STOP:
                    if state not in (J.NEEDS_REVIEW,) and J.NEEDS_REVIEW in _legal(state):
                        self.jobs.transition(job.id, J.NEEDS_REVIEW, f"quarantined: {decision.reason}")
                    if acct:
                        self.db.execute("UPDATE accounts SET review_status = 'quarantined', review_reason = ? WHERE id = ?",
                                        (decision.reason[:300], acct["id"]))
                    self.controls.stop(f"data integrity failure: {decision.reason}")
                elif a is Action.RECONCILE and state is not J.DELIVERY_UNKNOWN and J.DELIVERY_UNKNOWN in _legal(state):
                    self.jobs.transition(job.id, J.DELIVERY_UNKNOWN, decision.reason)
                elif a is Action.RECONCILE and state in PRE_SIDE_EFFECT_STATES:
                    # Resume found an unknown outbox: put the job back where reconciliation can see it.
                    self.jobs.transition(job.id, J.NEEDS_REVIEW, decision.reason)
                elif state in PRE_SIDE_EFFECT_STATES:
                    self.jobs.transition(job.id, J.NEEDS_REVIEW, f"unhandled: {decision.reason}")
        except Exception:
            # If even recording the failure fails (e.g. database down), leave the lease to expire:
            # stale-lease recovery will requeue or reconcile the job. Durable work is never dropped.
            log.exception("could not record failure; leaving job for lease recovery", extra={"job_id": job.id})
            raise

    def _platform_failure(self, exc: BaseException, where: str) -> None:
        decision = self.recovery.decide(exc=exc, subject=f"platform:{where}", attempts=1, category_count=1, account_failures=0)
        self.recovery.record(decision, scope="platform", job_id=None, subject=where)
        if decision.action is Action.PAUSE_APP:
            self.controls.pause(decision.reason)
        elif decision.action is Action.QUARANTINE_STOP:
            self.controls.stop(decision.reason)
        elif decision.action is Action.OPEN_CIRCUIT and decision.category is ErrorCategory.RATE_LIMITED:
            self.circuit.force_open(decision.reason)

    def _reset_account_failures(self, job: Job) -> None:
        self.db.execute("UPDATE accounts SET consecutive_failures = 0 WHERE username = ?", (job.subject_key,))

    def recover_stale(self) -> list[str]:
        """Expired leases: requeue pre-side-effect jobs; for jobs caught mid-send, also mark the in-flight outbox
        attempt as unknown so the reconciler picks it up even if the worker that started it never comes back."""
        recovered = self.jobs.recover_stale_leases()
        for job_id in recovered:
            if self.jobs.get(job_id).state is not J.DELIVERY_UNKNOWN:
                continue
            ob = self._existing_outbox(job_id)
            if ob and ob["state"] == OutboxState.ATTEMPTING.value:
                with self.db.transaction():
                    self._outbox_state(ob["id"], OutboxState.UNKNOWN)
                    self.db.execute("UPDATE delivery_attempts SET result = 'unknown', finished_at = ?, error = ? WHERE outbox_id = ? AND result IS NULL",
                                    (iso(), "lease expired mid-send (watchdog)", ob["id"]))
        return recovered

    def release_due_blocked(self) -> None:
        for r in self.db.all("SELECT id FROM jobs WHERE state = 'blocked' AND run_after IS NOT NULL AND run_after <= ?", (iso(),)):
            ob = self._existing_outbox(r["id"])
            # A blocked job with an approved outbox must resume sending, which ready_to_send handles via claim().
            self.jobs.transition(r["id"], J.QUEUED, "block expired")

    # ------------------------------------------------------------------ watchdog
    def watchdog(self) -> list[str]:
        """Detect jobs that stopped making progress. Expiring the lease hands them to stale-lease recovery,
        which requeues pre-side-effect work and sends in-flight sends to reconciliation."""
        flagged = []
        lim = self.s.limits
        for r in self.db.all("SELECT * FROM jobs WHERE lease_owner IS NOT NULL"):
            progress, started, since = parse(r["progress_at"]), parse(r["started_at"]), parse(r["state_since"])
            t = now()
            why = None
            if progress and (t - progress).total_seconds() > lim.stall_s:
                why = f"no progress for {(t - progress).total_seconds():.0f}s"
            elif since and (t - since).total_seconds() > lim.max_state_s:
                why = f"in {r['state']} for {(t - since).total_seconds():.0f}s"
            elif started and (t - started).total_seconds() > lim.max_job_s:
                why = "exceeded total execution time"
            if why:
                with self.db.transaction():
                    self.db.execute("UPDATE jobs SET lease_expires_at = ? WHERE id = ?", (iso(t - timedelta(seconds=1)), r["id"]))
                    self.db.execute("INSERT INTO recovery_events(at, scope, job_id, subject, category, decision, detail) VALUES (?, 'job', ?, ?, ?, ?, ?)",
                                    (iso(), r["id"], r["subject_key"], "transient", "watchdog_expire_lease", json.dumps({"reason": why})))
                metrics.inc("jobs_stalled")
                flagged.append(r["id"])
        if flagged:
            self.recover_stale()
        return flagged

    # ------------------------------------------------------------------ operator actions
    def approve_draft(self, draft_id: str, text: str | None = None, actor: str = "operator") -> None:
        """Approve (optionally edited) text for one recipient. The approval is bound to that recipient and text
        (approved_hash), expires after policy.approval_ttl_hours, and is re-validated against the conversation
        right before sending."""
        from ..intel.reasoning import check_text
        with self.db.transaction():
            d = self.db.one("SELECT * FROM drafts WHERE id = ?", (draft_id,))
            if d is None:
                raise PolicyViolation("no such draft")
            if d["status"] != "pending":
                raise PolicyViolation(f"draft is {d['status']}; only the newest pending draft can be approved")
            job = self.jobs.get(d["job_id"])
            if job.state not in (J.AWAITING_APPROVAL, J.NEEDS_REVIEW):
                raise PolicyViolation(f"job is {job.state.value}; approvals are only accepted while it waits for one")
            if d["target"].lower() != job.subject_key.lower():
                raise DataIntegrityError("draft recipient does not match its job")
            final = (text or d["text"]).strip()
            if problems := check_text(final):
                raise PolicyViolation(f"edited text breaks the rules: {', '.join(problems)}")
            ob = self._existing_outbox(job.id)
            ts = iso()
            if ob is None:
                self._create_outbox(job, draft_id, d["target"], final, approved_by=actor)
            else:
                if ob["state"] not in (OutboxState.PENDING.value, OutboxState.FAILED.value):
                    raise DataIntegrityError(f"outbox is {ob['state']}; an attempted message can't be changed")
                self.db.execute("UPDATE outbox SET draft_id = ?, text = ?, approved_by = ?, approved_at = ?, approved_hash = ?, updated_at = ? WHERE id = ?",
                                (draft_id, final, actor, ts, self.approval_hash(ob["target"], final), ts, ob["id"]))
            self.db.execute("UPDATE drafts SET status = 'approved', text = ?, decided_by = ?, decided_at = ? WHERE id = ?",
                            (final, actor, ts, draft_id))
            if job.state is J.NEEDS_REVIEW:
                self.jobs.transition(job.id, J.QUEUED, "operator approved a reviewed draft")
            else:
                self.jobs.transition(job.id, J.READY_TO_SEND, "operator approved")
            if job.kind == "intro":
                self._mark_intro(job, "pending")
            self.db.audit(actor, "approve_draft", {"draft": draft_id, "edited": text is not None and final != d["text"]})

    def reject_draft(self, draft_id: str, actor: str = "operator") -> None:
        with self.db.transaction():
            d = self.db.one("SELECT * FROM drafts WHERE id = ?", (draft_id,))
            job = self.jobs.get(d["job_id"])
            if job.state not in (J.AWAITING_APPROVAL, J.NEEDS_REVIEW):
                raise PolicyViolation(f"job is {job.state.value}; it can't be rejected now")
            self.db.execute("UPDATE drafts SET status = 'rejected', decided_by = ?, decided_at = ? WHERE id = ?", (actor, iso(), draft_id))
            ob = self._existing_outbox(d["job_id"])
            if ob and ob["state"] == "pending":
                self._outbox_state(ob["id"], OutboxState.CANCELLED)
            self.jobs.transition(d["job_id"], J.SKIPPED, "operator rejected the draft")
            if job.kind == "intro":
                self._mark_intro(job, "skipped")
            self.db.audit(actor, "reject_draft", {"draft": draft_id})

    def resolve_unknown(self, outbox_id: str, delivered: bool, actor: str = "operator") -> None:
        """Operator decision for a message whose delivery could not be established."""
        ob = self.db.one("SELECT * FROM outbox WHERE id = ?", (outbox_id,))
        if ob["state"] != OutboxState.UNKNOWN.value:
            raise DataIntegrityError(f"outbox is {ob['state']}, not unknown")
        job = self.jobs.get(ob["job_id"])
        with self.db.transaction():
            self.db.audit(actor, "resolve_unknown", {"outbox": outbox_id, "delivered": delivered})
            if delivered:
                self._outbox_state(outbox_id, OutboxState.SENT)
            else:
                self._outbox_state(outbox_id, OutboxState.FAILED)
                self.jobs.transition(job.id, J.QUEUED if job.state is J.NEEDS_REVIEW else J.RETRY_SCHEDULED,
                                     "operator confirmed not delivered; resend allowed")
                self._mark_intro(job, "pending")
        if delivered:
            self._complete_sent(job.id, outbox_id)

    # ------------------------------------------------------------------ memory and journal
    def _remember(self, target: str, kind: str, content: str, source: str, confidence: float | None) -> None:
        self.db.execute("INSERT INTO memory(id, target, kind, content, source, confidence, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (new_id("mem"), target, kind, content, source, confidence, iso()))

    def _journal(self, target: str | None, observation: str, basis: str, uncertainty: str | None) -> None:
        self.db.execute("INSERT INTO journal(id, target, observation, basis, uncertainty, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (new_id("jrn"), target, observation, basis, uncertainty, iso()))

    def forget(self, username: str, actor: str = "operator") -> int:
        with self.db.transaction():
            n = self.db.execute("DELETE FROM memory WHERE target = ?", (username,)).rowcount
            self.db.audit(actor, "forget", {"target": username, "rows": n})
            return n

    def apply_retention(self) -> int:
        cutoff = iso(now() - timedelta(days=self.s.policy.retention_days))
        with self.db.transaction():
            n = self.db.execute("DELETE FROM memory WHERE created_at < ?", (cutoff,)).rowcount
            n += self.db.execute("DELETE FROM journal WHERE created_at < ?", (cutoff,)).rowcount
            return n


def _legal(state: JobState) -> set[JobState]:
    from ..core.states import JOB_TRANSITIONS
    return JOB_TRANSITIONS[state]


def _info(reason: str, category: ErrorCategory = ErrorCategory.DELIVERY_UNKNOWN):
    from ..core.recovery import Decision
    return Decision(Action.RECONCILE, category, reason)
