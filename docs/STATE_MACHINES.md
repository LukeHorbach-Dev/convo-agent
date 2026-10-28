# State machines

All transitions are validated in code (`agent/core/states.py`); illegal transitions raise `DataIntegrityError`.
Every job transition is persisted in `job_transitions` with a reason.

## Processing job

```mermaid
stateDiagram-v2
  [*] --> queued
  queued --> claimed: worker claims (CAS + lease)
  claimed --> inspecting: verify account + conversation identity
  inspecting --> reasoning: context loaded
  reasoning --> awaiting_approval: real platform / approval policy
  reasoning --> ready_to_send: mock auto-approval
  reasoning --> needs_review: validation failed / injection / model asked for a human
  awaiting_approval --> ready_to_send: operator approves
  ready_to_send --> claimed: worker re-verifies before sending
  ready_to_send --> sending: attempt committed
  ready_to_send --> blocked: daily limit
  sending --> verifying: platform confirmed
  sending --> delivery_unknown: crash / timeout / no confirmation
  sending --> retry_scheduled: platform confirmed rejection
  verifying --> completed
  delivery_unknown --> completed: reconciliation found the message
  delivery_unknown --> retry_scheduled: authoritative check says not delivered
  delivery_unknown --> needs_review: can't establish (no automatic resend)
  claimed --> retry_scheduled: transient failure
  inspecting --> skipped: account-specific / permission
  retry_scheduled --> queued: delay elapsed
  blocked --> queued: block expired
  needs_review --> queued: operator retry / approval
  needs_review --> skipped
  completed --> [*]
  skipped --> [*]
  failed --> [*]
  cancelled --> [*]
```

Restart rules (`Engine.startup_recovery`):

| State at crash | After restart | Why |
|---|---|---|
| claimed, inspecting, reasoning | queued | no side effect has happened yet |
| sending, verifying | delivery_unknown | the send may have happened: reconcile, never resend blindly |
| outbox `attempting` | outbox `unknown`, attempt marked `unknown` | same |
| awaiting_approval, ready_to_send, retry_scheduled, blocked, needs_review | unchanged | already durable and safe |

Lease expiry (`JobQueue.recover_stale_leases`) applies the same rules while running; the watchdog forces lease
expiry for jobs with no progress for `stall_s`, in one state for `max_state_s`, or running longer than `max_job_s`.

## Outbound message (outbox)

```mermaid
stateDiagram-v2
  [*] --> pending: persisted (approved or awaiting approval)
  pending --> attempting: attempt row committed before the platform call
  pending --> cancelled: draft rejected
  attempting --> sent: confirmed
  attempting --> failed: confirmed rejection
  attempting --> unknown: outcome can't be established
  unknown --> sent: reconciliation found it / operator confirms
  unknown --> failed: authoritative check / operator says not delivered
  failed --> pending: re-queued (no duplicate risk: confirmed not delivered)
  sent --> [*]
  cancelled --> [*]
```

`unknown` can never go back to `pending` directly: only through `failed`, which requires an authoritative check
or an explicit operator decision.

## Browser

```mermaid
stateDiagram-v2
  [*] --> stopped
  stopped --> starting
  starting --> ready
  starting --> failed
  ready --> degraded: health check fails / crash detected
  ready --> auth_required: login wall or challenge
  degraded --> recovering
  recovering --> ready: restart succeeded (within budget)
  recovering --> failed: budget exhausted -> app pauses
  auth_required --> ready: operator signed in, then resumed
```

## Conversation

`active` → `opted_out` (they asked to stop: permanent, no further contact) or `needs_review` (e.g. stated age
outside the allowed range). Opted-out conversations never get reply jobs; the account is never re-introduced.
