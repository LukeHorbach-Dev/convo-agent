# Test report

Latest run after the release review (Python 3.12, Playwright 1.63 / Chromium 153, Linux), from a clean copy of
the git-tracked files installed only from `requirements.lock`. The raw output is in
[evidence/clean-install.txt](evidence/clean-install.txt), and the live approval run through the real CLI and HTTP
API in [evidence/live-approval-run.txt](evidence/live-approval-run.txt).

```
$ .venv/bin/python -m pytest
218 passed
$ CHAOS_SEEDS=300 .venv/bin/python -m pytest tests/test_chaos.py
301 passed
```

| File | Tests | Kind |
|---|---|---|
| `tests/test_unit_core.py` | 65 | unit: state machines, classification, backoff, circuit breaker, controls, policy, job queue (including concurrent claims on separate connections) |
| `tests/test_reasoning.py` | 18 | unit: profile retrieval, draft validation, invented facts, style/policy checks, injection, model failures, intro rotation |
| `tests/test_faults.py` | 36 | integration and fault injection on the mock platform |
| `tests/test_browser.py` | 14 | end to end and fault injection with a real Chromium against the mock site |
| `tests/test_dashboard.py` | 12 | API auth, host guard, CSRF, controls, approval flow, unknown-delivery resolution, sanitized diagnostics |
| `tests/test_chaos.py` | 41 | 40 randomized fault-injection seeds plus a check that the faults actually fired (including mid-send watchdog takeovers) |
| `tests/test_review_findings.py` | 26 | regression tests for the release-review findings R1 to R13 ([REVIEW.md](REVIEW.md)), each observed failing before its fix |
| `tests/test_operator_features.py` | 6 | never-list, list import, plain-English translation, dashboard XSS (real Chromium) |

## Mandatory fault scenarios

| # | Scenario | Test(s) | Asserted outcome |
|---|---|---|---|
| 1 | page never finishes loading | `test_navigation_timeout_is_classified` | `platform_unavailable`, bounded by navigation timeout |
| 2 | browser crash during navigation | `test_browser_crash_recovers`, `test_browser_recovery_budget_exhausted_pauses`, `test_engine_pauses_when_browser_budget_exhausted` | restart within budget; app paused when exhausted; nothing sent |
| 3 | disconnect between reading and processing | `test_disconnect_while_reading_retries` | `retry`/`transient` recorded, reply completes once |
| 4 | selector disappears after a UI change | `test_selector_drift_detected`, `test_selector_drift_everywhere_opens_circuit`, `test_repeated_account_error_quarantines_only_that_account` | drift classified; one account → needs_review; everywhere → circuit open |
| 5 | login session expires | `test_expired_session_pauses_app_and_never_sends`, `test_session_expiry_detected_and_engine_pauses`, `test_wrong_logged_in_account_fails_closed` | app paused with reason; stays paused until operator resumes |
| 6 | message button unavailable | `test_missing_message_control_is_recorded_not_circumvented`, `test_missing_message_button` | skipped with reason; app keeps running |
| 7 | conversation not uniquely identifiable | `test_ambiguous_conversation_retries_then_needs_review`, `test_ambiguous_thread` | ambiguous_state; never sent |
| 8 | model provider timeout | `test_model_timeout_retries_then_succeeds`, `test_model_timeout_raises_model_failure` | 2 recorded retries then success |
| 9 | malformed model response | `test_model_keeps_failing_ends_in_review_not_loop`, `test_malformed_model_output_raises_model_failure` | bounded attempts, ends in review |
| 10 | database write failure | `test_database_failure_does_not_lose_work` | transaction rolled back, retried, completed once |
| 11 | crash after saving an outbox record | `test_crash_after_outbox_saved_before_send` | restart → delivery_unknown → reconciled → sent exactly once |
| 12 | crash right after a possible send | `test_crash_right_after_delivery_never_resends`, `test_unconfirmed_send_is_reconciled_not_resent` (browser) | never resent; reconciled as sent |
| 13 | event delivered twice | `test_duplicate_follower_event_creates_one_job`, `test_duplicate_incoming_message_one_reply` | one job, one message |
| 14 | two workers claim the same job | `test_two_workers_cannot_claim_the_same_job`, `test_same_subject_serialized`, `test_lease_owner_enforced`, `test_second_scheduler_instance_refused` | exactly one claim; second scheduler refused |
| 15 | queue over capacity | `test_queue_overflow_applies_backpressure_and_catches_up`, `test_queue_capacity_backpressure` | backpressure recorded; nothing lost |
| 16 | repeated account-specific error | `test_repeated_account_error_quarantines_only_that_account` | that account needs_review; others complete; circuit closed |
| 17 | platform-wide outage | `test_platform_wide_failure_opens_circuit`, `test_outage_during_ingestion_feeds_circuit_and_recovers`, `test_site_outage_and_rate_limit` | circuit open; zero platform calls while open |
| 18 | job stops making progress | `test_stalled_job_detected_by_watchdog`, `test_stall_during_send_goes_to_reconciliation_not_resend` | lease expired; requeued, or reconciled if it was sending |
| 19 | emergency stop during processing | `test_emergency_stop_mid_job_prevents_send`, `test_emergency_stop_blocks_and_requires_explicit_clear`, `test_controls_round_trip` | no send; audit record; explicit clear required |
| 20 | prompt injection | `test_prompt_injection_is_held_for_review`, `test_injection_is_never_obeyed_and_is_escalated` | needs_review; never auto-sent |
| 21 | request to stop receiving messages | `test_stop_request_is_respected_forever` | opted out; no reply; no re-introduction |
| 22 | restart with jobs in ambiguous states | `test_restart_with_mixed_ambiguous_states` | pre-side-effect requeued; mid-send never resent |

Additional: rate limiting (`test_rate_limit_opens_circuit_and_stops_platform_work`), identity conflict
(`test_identity_conflict_quarantines_and_stops`), real-platform approval gate (3 tests), draft mode, daily limit,
operator edit validation, memory export/forget/retention, inferred-follower baseline (`test_end_to_end_engine_through_browser`).

## Chaos invariants (each of 40 seeds; 300 in the long run)

Each seed runs 30 ticks with random outages, UI drift, send failures, lost confirmations, model failures,
duplicate events, process crashes before and after delivery (with restart recovery), and the watchdog taking a
job over in the middle of a send, then a calm period.
Asserted against the mock platform's ground truth:

1. no outbox operation delivered twice;
2. no delivered operation ever recorded as failed or pending (which would allow a resend);
3. at most one introduction per person;
4. no outbox row left `attempting` after restart recovery;
5. every follower ends with a job or a recorded reason.

Fault coverage over 10 seeds: 38 simulated process crashes, 162 deliveries, 53 reconciled uncertain sends.

## Bugs the tests found and fixed during development

* Keyword retrieval picked a fact on a stopword ("love"); now uses stopwords and prefix matching.
* A database I/O error was classed as data corruption and stopped the app; operational errors are now transient
  (the transaction rolled back) and only integrity/corruption errors stop it.
* After reconciliation confirmed "not delivered", the retry used a fixed 5 s delay; it now uses configured backoff.
* An emergency stop while a job was `ready_to_send` hit an illegal transition; `ready_to_send → queued` is now legal
  (safe: nothing was attempted, and jobs resume from the outbox).
* Browser restart budget exhaustion raised the same error as an operator stop, so the app didn't pause; it now
  raises `InterventionRequired`, which pauses with an explanation.
* Operator-confirmed deliveries were completed a tick late; they now complete immediately.
* The first editable install ran before the package existed (app failed to start); the README now installs in the
  right order, verified in a fresh environment.

## Coverage versus guarantees

Passing tests show the behaviour on the scenarios exercised; they are not proofs over all interleavings. See
"Coverage is not proof" in [REVIEW.md](REVIEW.md) for what is untested.
