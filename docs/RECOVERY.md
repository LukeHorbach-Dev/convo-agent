# Recovery

## Error categories

| Category | Raised for | Feeds circuit breaker |
|---|---|---|
| `transient` | network blips, browser crash/disconnect, stale page context, database locked/busy/I/O, queue full | yes |
| `permanent` | unknown exceptions, rejected model requests (4xx) | no |
| `authentication` | login wall, verification challenge, wrong logged-in account | no |
| `permission` | no Message control, messaging restricted, policy refused a send | no |
| `rate_limited` | HTTP 429 or platform "try again later" | yes (opens immediately) |
| `platform_unavailable` | 5xx, page never loads | yes |
| `selector_drift` | expected element missing, composer refuses text | yes |
| `ambiguous_state` | conversation or participant can't be identified uniquely, unexpected redirect | yes |
| `delivery_unknown` | send outcome can't be established | no |
| `data_integrity` | identity conflict, illegal transition, lost lease, constraint violation, corrupt DB | no |
| `model_failure` | model timeout, outage, invalid structured output | no |
| `operator_intervention_required` | browser restart budget exhausted, API key rejected, operator stop | no |

## Decision table (`RecoveryManager.decide`)

Evaluated top to bottom; the first match wins.

| # | Condition | Decision | Effect |
|---|---|---|---|
| 1 | operator stop / emergency stop | `stop_app` | job held (queued); nothing else happens |
| 2 | `data_integrity` | `quarantine_stop` | job → needs_review, account quarantined, **application stopped** (Case E) |
| 3 | `delivery_unknown` | `reconcile` | job → delivery_unknown; reconciler checks, never resends (Case F) |
| 4 | `authentication` | `pause_app` | job held, **application paused** with the reason (Case C) |
| 5 | `permission` or `permanent` | `skip_account` | job skipped with the reason; others continue (Case B) |
| 6 | `operator_intervention_required` | `pause_app` | application paused with the reason |
| 7 | platform category opens the breaker (N distinct accounts, same category, within window) | `open_circuit` | platform work stops until cooldown + successful probe (Case D) |
| 8 | `rate_limited` | `open_circuit` | breaker forced open; no evasion, no retry until cooldown |
| 9 | `selector_drift` / `ambiguous_state` repeated on the same account | `skip_account` | account → needs_review; not revisited this run |
| 10 | retryable category within budget | `retry` | bounded exponential backoff with jitter, honouring retry-after (Case A) |
| 11 | retry budget exhausted | `skip_account` | needs_review with the last error |

Every decision is written to `recovery_events` and shown on the dashboard. Platform-level failures during ingestion
(outside any job) go through the same table with scope `platform`.

## Scopes: skip, pause, stop

* **Skip account**: an isolated account can't be processed safely. Reason persisted; others continue if the
  breaker is closed.
* **Pause application**: login needed, browser budget exhausted, operator decision needed. No new work starts;
  all jobs stay durable; the dashboard shows why; **Resume** continues.
* **Stop application**: data integrity failure or operator stop. Same as pause, but needs an explicit resume.
  **Emergency stop** additionally requires an explicit clear, persists across restarts, and is re-checked inside
  the transaction that commits a send attempt.

No recovery policy overrides an operator stop or a platform restriction.

## Delivery guarantees

The platform offers no idempotency key, so **exactly-once delivery is not possible**. What the outbox guarantees:

1. The attempt is committed before the platform call (`outbox.state = attempting`, a `delivery_attempts` row).
2. If anything goes wrong after that point, including a process crash, the outcome is recorded as **unknown**,
   never as failed.
3. Unknown outcomes are reconciled by re-reading the conversation:
   * found → `sent`.
   * absent, and the adapter's check is **authoritative** (mock platform) → `failed`, eligible for resend.
   * absent, and the check is **best effort** (web adapters, Instagram) → stays `unknown`; after 3 checks the job
     goes to **needs_review**. You decide on the dashboard ("It was sent" / "Not sent").
4. An introduction whose outcome is unknown blocks any further introduction to that person.

So: **at most once** for messages whose outcome can't be confirmed, with a human deciding the rare ambiguous case.
Best-effort reconciliation looks for the exact text in the thread, so an identical earlier message could make an
undelivered one look delivered. That errs toward *not* resending, deliberately.
