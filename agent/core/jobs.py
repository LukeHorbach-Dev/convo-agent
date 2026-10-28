"""Durable job queue with leases, per-subject serialization, deduplication and backpressure."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta

from .config import LimitsConfig
from .db import Database, iso, new_id, now, parse
from .errors import QueueFull
from .states import JOB_TRANSITIONS, PRE_SIDE_EFFECT_STATES, TERMINAL_JOB_STATES, JobState, check


@dataclass
class Job:
    id: str
    kind: str
    subject_key: str
    state: JobState
    attempts: int
    payload: dict
    retries_by_category: dict

    @classmethod
    def from_row(cls, r) -> "Job":
        return cls(r["id"], r["kind"], r["subject_key"], JobState(r["state"]), r["attempts"],
                   json.loads(r["payload"]), json.loads(r["retries_by_category"]))


ACTIVE_STATES = [s.value for s in JobState if s not in TERMINAL_JOB_STATES]
LEASED_STATES = [JobState.CLAIMED, JobState.INSPECTING, JobState.REASONING, JobState.SENDING, JobState.VERIFYING]


class JobQueue:
    def __init__(self, db: Database, limits: LimitsConfig):
        self.db = db
        self.limits = limits

    def depth(self) -> int:
        return self.db.scalar(f"SELECT COUNT(*) FROM jobs WHERE state IN ({','.join('?' * len(ACTIVE_STATES))})", ACTIVE_STATES)

    def enqueue(self, kind: str, subject_key: str, dedup_key: str, payload: dict | None = None) -> str | None:
        """Returns the new job id, or None if an identical job already exists (duplicate prevented)."""
        with self.db.transaction():
            if self.db.one("SELECT 1 FROM jobs WHERE dedup_key = ?", (dedup_key,)):
                return None
            if self.depth() >= self.limits.queue_capacity:
                raise QueueFull(f"queue at capacity ({self.limits.queue_capacity})")
            job_id = new_id("job")
            ts = iso()
            self.db.execute(
                "INSERT INTO jobs(id, kind, subject_key, dedup_key, state, state_since, payload, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                (job_id, kind, subject_key, dedup_key, ts, json.dumps(payload or {}), ts, ts))
            self._record(job_id, None, JobState.QUEUED, "enqueued")
            return job_id

    def get(self, job_id: str) -> Job:
        return Job.from_row(self.db.one("SELECT * FROM jobs WHERE id = ?", (job_id,)))

    def claim(self, owner: str, kinds: list[str] | None = None, hold_new: list[str] | None = None) -> Job | None:
        """Atomically claim the next eligible job. A subject with another leased job is skipped,
        so two messages for the same conversation never run concurrently. Jobs of a kind in `hold_new` are only
        claimed if already approved (ready_to_send), not started fresh."""
        ts = iso()
        lease = iso(now() + timedelta(seconds=self.limits.lease_s))
        leased = ",".join("?" * len(LEASED_STATES))
        kind_sql = f"AND kind IN ({','.join('?' * len(kinds))})" if kinds else ""
        if hold_new:
            kind_sql += f" AND NOT (state = 'queued' AND kind IN ({','.join('?' * len(hold_new))}))"
        with self.db.transaction():
            row = self.db.one(
                f"""SELECT * FROM jobs j WHERE state IN ('queued', 'ready_to_send')
                    AND (run_after IS NULL OR run_after <= ?) {kind_sql}
                    AND NOT EXISTS (SELECT 1 FROM jobs o WHERE o.subject_key = j.subject_key AND o.id != j.id
                                    AND o.state IN ({leased}) AND o.lease_expires_at > ?)
                    ORDER BY CASE state WHEN 'ready_to_send' THEN 0 ELSE 1 END, created_at LIMIT 1""",
                (ts, *(kinds or []), *(hold_new or []), *LEASED_STATES, ts))
            if not row:
                return None
            # Compare-and-set on state: if another worker claimed it between SELECT and UPDATE, rowcount is 0.
            cur = self.db.execute(
                "UPDATE jobs SET state = 'claimed', state_since = ?, lease_owner = ?, lease_expires_at = ?, heartbeat_at = ?, "
                "progress_at = ?, started_at = COALESCE(started_at, ?), attempts = attempts + 1, updated_at = ? "
                "WHERE id = ? AND state = ?",
                (ts, owner, lease, ts, ts, ts, ts, row["id"], row["state"]))
            if cur.rowcount != 1:
                return None
            payload = json.loads(row["payload"])
            payload["resume_from"] = row["state"]
            self.db.execute("UPDATE jobs SET payload = ? WHERE id = ?", (json.dumps(payload), row["id"]))
            self._record(row["id"], row["state"], JobState.CLAIMED, f"claimed by {owner}")
            return self.get(row["id"])

    def transition(self, job_id: str, target: JobState, reason: str, *, owner: str | None = None, **fields) -> None:
        with self.db.transaction():
            row = self.db.one("SELECT state, lease_owner FROM jobs WHERE id = ?", (job_id,))
            current = JobState(row["state"])
            if owner is not None:
                # A worker may only move a job it still holds: a leased state with its own lease, or ready_to_send
                # (where it just put it). Anything else means recovery took the job over while we were busy.
                held = (current in LEASED_STATES and row["lease_owner"] == owner) or current is JobState.READY_TO_SEND
                if not held:
                    from .errors import LeaseLost
                    raise LeaseLost(f"job {job_id} is {current.value} (lease {row['lease_owner']!r}); worker {owner!r} no longer owns it")
            check(JOB_TRANSITIONS, current, target)
            ts = iso()
            sets = {"state": target.value, "state_since": ts, "updated_at": ts, "progress_at": ts, **fields}
            if target not in LEASED_STATES:
                sets.update(lease_owner=None, lease_expires_at=None)
            elif owner is not None:
                # Entering a leased state (e.g. ready_to_send -> sending) takes the lease so the watchdog can see it.
                sets.update(lease_owner=owner, lease_expires_at=iso(now() + timedelta(seconds=self.limits.lease_s)), heartbeat_at=ts)
            cols = ", ".join(f"{k} = ?" for k in sets)
            self.db.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*sets.values(), job_id))
            self._record(job_id, current, target, reason)

    def heartbeat(self, job_id: str, owner: str, progressed: bool = True) -> bool:
        ts = iso()
        lease = iso(now() + timedelta(seconds=self.limits.lease_s))
        sql = "UPDATE jobs SET heartbeat_at = ?, lease_expires_at = ?" + (", progress_at = ?" if progressed else "") + \
              " WHERE id = ? AND lease_owner = ?"
        params = (ts, lease, ts, job_id, owner) if progressed else (ts, lease, job_id, owner)
        with self.db.transaction():
            return self.db.execute(sql, params).rowcount == 1

    def set_payload(self, job_id: str, **updates) -> None:
        with self.db.transaction():
            payload = json.loads(self.db.scalar("SELECT payload FROM jobs WHERE id = ?", (job_id,)))
            payload.update(updates)
            self.db.execute("UPDATE jobs SET payload = ?, updated_at = ? WHERE id = ?", (json.dumps(payload), iso(), job_id))

    def bump_category(self, job_id: str, category: str) -> int:
        with self.db.transaction():
            counts = json.loads(self.db.scalar("SELECT retries_by_category FROM jobs WHERE id = ?", (job_id,)))
            counts[category] = counts.get(category, 0) + 1
            self.db.execute("UPDATE jobs SET retries_by_category = ? WHERE id = ?", (json.dumps(counts), job_id))
            return counts[category]

    def release_due_retries(self) -> int:
        ts = iso()
        n = 0
        for r in self.db.all("SELECT id FROM jobs WHERE state = 'retry_scheduled' AND (run_after IS NULL OR run_after <= ?)", (ts,)):
            self.transition(r["id"], JobState.QUEUED, "retry delay elapsed")
            n += 1
        return n

    def recover_stale_leases(self) -> list[str]:
        """Leases that expired in a pre-side-effect state are requeued. Expired leases in SENDING/VERIFYING
        are moved to delivery_unknown: the send may have happened, so they need reconciliation, never a resend."""
        ts = iso()
        recovered = []
        for r in self.db.all("SELECT id, state FROM jobs WHERE lease_expires_at IS NOT NULL AND lease_expires_at < ?", (ts,)):
            state = JobState(r["state"])
            if state in PRE_SIDE_EFFECT_STATES:
                self.transition(r["id"], JobState.QUEUED, "lease expired before any side effect; requeued")
            elif state in (JobState.SENDING, JobState.VERIFYING):
                self.transition(r["id"], JobState.DELIVERY_UNKNOWN, "lease expired during send; needs reconciliation")
            recovered.append(r["id"])
        return recovered

    def _record(self, job_id: str, frm, to: JobState, reason: str) -> None:
        self.db.execute("INSERT INTO job_transitions(job_id, from_state, to_state, reason, at) VALUES (?, ?, ?, ?, ?)",
                        (job_id, frm.value if isinstance(frm, JobState) else frm, to.value, reason, iso()))

    def is_stalled(self, row, stall_s: float) -> bool:
        progress = parse(row["progress_at"])
        return progress is not None and (now() - progress).total_seconds() > stall_s
