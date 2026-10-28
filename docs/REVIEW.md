# Release-readiness review

Date: 2026-10-28. Scope: the whole `convo-agent/` application, its reports and tests. The Chrome extension was not
modified (its files were only read, to copy your personal list).

Method: I read every module against the claims in `REPORT.md`, `docs/TEST_REPORT.md` and `docs/LIMITATIONS.md`.
For each suspected defect I first wrote a test reproducing it and **ran it against the unmodified code**. I only
recorded a finding if that test failed: 16 of 23 probes failed before the fixes (log below). Each fix was then
verified by the same test, and the complete suite was re-run from a clean copy containing only git-tracked files.

## Findings

| ID | Severity | Finding | Status | Regression test |
|---|---|---|---|---|
| R1 | **High** | **A send that outlived its lease could leave a delivered message stuck and stop the app.** The watchdog moved the job to `delivery_unknown` but left the outbox row in `attempting`, which the reconciler never scans. When the worker's send returned, its state update was illegal and was classified as data corruption, stopping the whole app. | Fixed: stale-lease recovery also marks the in-flight attempt `unknown`; outcome recording tolerates a takeover (the observed result wins); added mid-send watchdog takeovers to the chaos suite. | `test_slow_send_outliving_lease_is_reconciled_not_stuck`, chaos (`watchdog_mid_send` guard) |
| R2 | **High** | **Approvals didn't expire and weren't re-checked against the conversation.** An approved reply would still be sent after the person said "please stop", after newer messages arrived, or days later. | Fixed: approvals expire after `POLICY__APPROVAL_TTL_HOURS` (24); before sending, the thread is re-read and the approval is cancelled if the person opted out, is on the never-list, or the conversation moved on. | `test_approved_reply_not_sent_after_person_opts_out`, `test_approved_reply_superseded_by_newer_message`, `test_expired_approval_is_not_used`, `test_adding_someone_to_never_list_cancels_their_approved_message` |
| R3 | **High** | **The approval gate could be bypassed by misconfiguration.** `mock_web` is treated as "not real" (autonomous sending), but `MOCK_SITE_URL` accepted any URL, including a real website. | Fixed: config validation accepts only loopback http URLs; the CLI validates before starting anything. | `test_mock_site_url_must_be_local[...]`, `test_cli_refuses_non_local_mock_site_before_starting_anything` |
| R4 | Medium | **An approval wasn't bound to the exact text and recipient.** The text could change after approval (direct DB edit or a race) and the changed text would be sent. Approval checks ran outside the transaction (time-of-check/time-of-use), and old drafts of the same job stayed approvable. | Fixed: `outbox.approved_hash` = sha256(recipient, text), verified inside the transaction that commits the attempt (migration 0002); approvals only for jobs waiting for one and only for the newest draft; older drafts marked `superseded`. | `test_tampered_outbox_text_is_refused`, `test_superseded_draft_cannot_be_approved`, `test_approval_refused_while_job_in_flight` |
| R5 | Medium | **A lost lease before sending stopped the app.** A slow model call past the lease made the worker's next state change look like data corruption. | Fixed: a `LeaseLost` error; the worker abandons the job and records it; whoever owns the job now continues. | `test_lease_lost_before_send_abandons_job_without_stopping_app`, `test_lease_owner_enforced` |
| R6 | Medium | **An approved message in draft mode re-ran every tick**, navigating the platform each time for a send that could never happen. | Fixed: parked as `blocked` with the reason; Retry after changing mode. | `test_draft_mode_approval_does_not_churn` |
| R7 | Medium | **Rejecting an intro left the person `pending` forever**, blocking them permanently with no visible reason. | Fixed: marked `skipped`. | `test_rejected_intro_is_marked_skipped` |
| R8 | Low | Retention didn't run while the app was paused, stopped, or the circuit was open. | Fixed: runs before those checks (local housekeeping only). | `test_retention_runs_while_paused` |
| R9 | Low | `data/agent.log` (contains usernames) and the scheduler lock weren't git-ignored. | Fixed. | `test_sensitive_paths_are_git_ignored[...]` |
| R10 | Low | The test fixture profile contained your real facts (hometown, travel, modelling) with only the name changed, and the fake model's canned reply named a place you've been. | Fixed: synthetic fixture; neutral canned text. | `test_test_fixture_profile_is_synthetic` |
| R11 | Low | Rejecting a draft for a job not awaiting a decision returned HTTP 500 instead of 409. | Fixed. | `test_reject_on_a_job_not_awaiting_a_decision_is_409` |
| R12 | Low | `pyproject.toml` allowed any `anthropic>=0.49` while the lockfile pinned 2.3.0. | Fixed: pinned. | n/a |
| R13 | Low | The gitignore test passed only inside a git checkout (failed in a clean copy). | Fixed: evaluates `.gitignore` in a throwaway repo. | itself |
| R14 | Low | Found during this round's new feature: putting message text inside an inline `onclick` would have allowed script injection in the dashboard. Caught before release. | Fixed before shipping: text lives only in escaped data attributes with one delegated handler. | `test_dashboard_does_not_execute_message_content` (real Chromium) |

Probes that **passed against the old code**, confirming existing controls: tampered recipient refused (thread
identity re-check), double approval refused, profile/DB/token/cookies/.env git-ignored.

Pre-fix probe log:

```
FAILED test_slow_send_outliving_lease_is_reconciled_not_stuck
FAILED test_lease_lost_before_send_abandons_job_without_stopping_app
FAILED test_approved_reply_not_sent_after_person_opts_out
FAILED test_approved_reply_superseded_by_newer_message
FAILED test_expired_approval_is_not_used
FAILED test_mock_site_url_must_be_local[https://www.instagram.com]
FAILED test_mock_site_url_must_be_local[http://10.0.0.5:8899]
FAILED test_mock_site_url_must_be_local[http://evil.example:80]
FAILED test_tampered_outbox_text_is_refused
FAILED test_superseded_draft_cannot_be_approved
FAILED test_draft_mode_approval_does_not_churn
FAILED test_rejected_intro_is_marked_skipped
FAILED test_retention_runs_while_paused
FAILED test_sensitive_paths_are_git_ignored[data/agent.log]
FAILED test_sensitive_paths_are_git_ignored[data/scheduler.lock]
FAILED test_test_fixture_profile_is_synthetic
16 failed, 7 passed
```

## Review checklist results

**Approval enforcement (server side).** The final gate runs inside the transaction that commits the attempt. It
checks, in order: the emergency stop and pause, outbox still `pending`, `approved_by = operator` on a real
platform, the content hash matching recipient and text, and the recipient matching the job. Before that, the
conversation is re-read: identity, opt-out, never-list, newer messages, and approval age. Approvals are accepted
only while the job waits for one, only for the newest draft, inside one transaction. Concurrent approvals:
the database lock serializes them, and the second gets 409 (verified live).

**Database and transactions.** Every multi-row change is one transaction (`BEGIN IMMEDIATE`). Unique constraints
dedupe follower events, messages, jobs and outbox rows (one per job). Claims are compare-and-set. Leases are
enforced on every worker transition (`LeaseLost`). The attempt row is committed before any platform call.

**Uncertain delivery.** Unknown outcomes are reconciled. Only an authoritative "absent" (mock) or an operator
decision permits a resend. Instagram's best-effort check escalates to you.

**Retention.** Scheduled once per 24 h inside the worker tick, regardless of pause or circuit state (tested).

**Git and diagnostics.** Ignored: profile, never-list, intro list, DB, log, token, lock, browser profile, `.env`
(tested). Diagnostics exclude the API key, message text and memory (tested). **They do include usernames** in error
messages, transition reasons and config (account, never-list). Treat a diagnostic bundle as personal data.

**Dashboard.** Loopback bind (validated), token on every API call (constant-time), Host allow-list,
header-based auth (not CSRF-able), no CORS, CSP and frame denial, escaped rendering (tested in a real browser). The
mock site server (only in `mock_web`) has no auth: it binds to loopback and only controls synthetic data.

**Mock cannot send real messages.** `mock` has no network code. `mock_web` is restricted to loopback.
`instagram` is `is_real`, forced into execute mode, and every send needs an operator approval with a matching hash.

## Coverage is not proof

The tests show these behaviours on the scenarios exercised. They don't prove them for all interleavings. Untested
or unprovable here:

* **Multi-process concurrency.** Tested with threads and separate SQLite connections. The app runs one worker,
  and a file lock prevents a second scheduler.
* **Real wall-clock watchdog races.** Simulated by firing the watchdog inline at chosen points (including mid-send
  in chaos). Not tested: arbitrary preemption timing between the watchdog thread and the worker.
* **Disk full, power loss, clock jumps.** Durability relies on SQLite WAL defaults. Timestamps are UTC ISO strings,
  so a wall-clock jump backwards could delay retries or trigger the watchdog early.
* **Instagram.** Never run against instagram.com. No permitted integration test is available, so no
  compatibility claim is made. Unhandled there: message requests, group threads, unsent or edited messages,
  threads longer than the visible window, and locale-specific button text (the locators assume English "Message"
  and "Send").
* **Live model.** Translation quality, tone, and how often valid replies get flagged are unmeasured. The
  validators catch rule breaks and invented facts. They can't judge whether a reply is good.
* **Chaos.** 300 seeds of 30 ticks on 8 accounts: a sample, not an exhaustive search.

## Decision

* **Mock and mock_web: ready.** All acceptance criteria are met with reproducible evidence (`docs/evidence/`).
* **Instagram, draft and approve mode: ready for a supervised trial, not for unattended use.** Run in `draft` mode
  first to confirm the locators read your account correctly, then `execute` with approval. Expect to fix locators.
  Instagram's terms restrict automated use, so your account could be actioned regardless of the approval gate.
* **Unattended autonomous messaging on Instagram:** not supported, by design.
