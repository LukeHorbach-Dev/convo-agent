# Operations

## First run on Instagram

1. `python -m agent login` and sign in, including any verification, in the window that opens. The session is
   saved in `data/browser-profile`.
2. `AGENT_PLATFORM=instagram python -m agent run`. Mode is forced to `execute`; every message waits for approval.
3. The first followers read is a **baseline**: existing followers are recorded, not introduced (unless
   `POLICY__INTRO_EXISTING_FOLLOWERS=true`).
4. Open the dashboard, review drafts, approve or edit, and watch the Delivery panel.

## Troubleshooting

| Symptom (dashboard) | Likely cause | What to do |
|---|---|---|
| Paused: "login required" / "asking for verification" | session expired or Instagram challenge | Sign in or complete verification **yourself** in the agent's browser window, then **Resume**. The agent never interacts with login or challenge pages. |
| Paused: "logged in as X, expected Y" | wrong account in the profile | Sign into the right account or fix `ACCOUNT_USERNAME`. |
| Circuit open: "N accounts failed with selector_drift" | Instagram changed its layout | Check `diagnose` output and the failing locator in Recent errors. Update `agent/platform/sites.py` (`InstagramProfile`), then **Close circuit**. |
| Circuit open: "rate limited" | the platform asked you to slow down | Wait out the cooldown. Lower `DAILY_SEND_LIMIT`. Never work around it. |
| Paused: "browser recovery budget exhausted" | Chrome keeps crashing or freezing | Check memory (`js_heap_mb` in health), close other Chrome instances using the profile, restart the app. |
| Delivery shows `unknown` | the send's outcome couldn't be confirmed | Open the conversation on your phone. Click **It was sent** or **Not sent**. Only "Not sent" allows a resend. |
| Many `needs_review` jobs with model_failure | model provider down or key problem | Check `health.model`. Jobs keep their state; **Retry** them later. |
| Worker "seconds since tick" keeps growing | worker hung | The watchdog expires stalled leases. If it persists, stop and restart the app (state is durable). |
| Startup error "another scheduler is already running" | second instance on the same database | Stop the other one (`data/scheduler.lock` holds its PID). |
| Duplicate-looking events | at-least-once delivery from the platform | Expected: `metrics.duplicate_events_prevented` counts them; they never create a second job. |

## Safe shutdown

Ctrl+C or `kill -TERM <pid>`: the HTTP server stops, the worker finishes its current step (each step is a
transaction), the browser closes, the lock is released, and the log ends with `"msg": "shutdown"` and an outcome.
Exit status 0 means clean, 2 means the worker didn't stop within 30 s (state is still consistent; restart recovers).

## After a crash

Just start the app again. `startup_recovery` runs before the worker:

* jobs interrupted before any side effect are requeued;
* jobs interrupted during a send become `delivery_unknown`, and their attempts are marked unknown;
* reconciliation runs on the next tick; anything it can't establish goes to **needs_review** for you.

The summary is in the audit log (`action = startup_recovery`) and the structured log.

## Corrupted local state

If SQLite reports a malformed database, the error is classified `data_integrity` and the app stops. Restore
`data/agent.db` from a backup (copy it while the app is stopped), or move it aside to start fresh. Starting
fresh loses the prior-contact ledger, so set `POLICY__INTRO_EXISTING_FOLLOWERS=false` to avoid re-introductions.

## Logs and diagnostics

* `--log-file` writes JSON lines to `data/agent.log`: component, job id, account, category, decision, duration,
  outcome. Secrets are redacted; message text isn't logged.
* `python -m agent diagnose` (or `GET /api/diagnostics`): sanitized config, versions, health, recent errors,
  transitions, recovery events, metrics. No credentials, message text or memory contents.
