# Security review

Scope: the local application, its dashboard, data at rest, the browser session, and model interactions.

## Findings and controls

| Area | Risk | Control | Verified by |
|---|---|---|---|
| Sending on a real platform | the agent messages people without your consent | `policy.assert_deliverable` refuses any real-platform send whose outbox row lacks `approved_by = operator`; no setting changes this | `test_real_platform_send_without_operator_approval_is_refused`, `test_real_platform_requires_operator_approval`, `test_real_platform_always_requires_approval_regardless_of_config` |
| Approval binding | approved text or recipient changes, or an approval outlives its context | sha256(recipient, text) stored at approval and verified inside the send transaction; approvals expire; the thread is re-read and the approval cancelled on opt-out, never-list or newer messages | `test_tampered_outbox_text_is_refused`, `test_approved_reply_not_sent_after_person_opts_out`, `test_expired_approval_is_not_used` |
| Mock pointed at a real site | autonomous sending without approval | `MOCK_SITE_URL` must be loopback | `test_mock_site_url_must_be_local` |
| Dashboard access | another local user or a website controls the agent | bound to localhost (validated); every API call needs `X-Agent-Token` (constant-time compare); token file mode 0600 | `test_requires_token` |
| CSRF | a website posts to `127.0.0.1:8765` | state changes require a custom header; no CORS is granted, so browsers can't send it cross-site | `test_state_changes_need_the_header_not_a_cookie` |
| DNS rebinding | a hostile domain resolves to 127.0.0.1 | Host header must be localhost | `test_rejects_foreign_host_header` |
| XSS in dashboard | message text or usernames rendered as HTML | every value passes through `esc()`; untrusted text only in escaped data attributes, never inline JS; CSP and `X-Frame-Options: DENY` | `test_dashboard_does_not_execute_message_content` (real Chromium) |
| Prompt injection | a message tells the model to ignore rules or leak data | messages wrapped as untrusted `<conversation>` data; system prompt forbids following them; heuristic detection forces human review; the model has no tools and can't act | `test_injection_is_never_obeyed_and_is_escalated`, `test_prompt_injection_is_held_for_review` |
| Invented personal facts | the model claims experiences you never had | drafts must cite fact ids; unverified, unknown or private ids and uncited first-person claims force review | `test_invented_fact_is_escalated`, `test_personal_claim_without_citation_is_escalated`, `test_private_fact_cannot_be_cited` |
| Secrets in logs | API key or session cookies leak | `SecretStr` for the key; log redaction for `sk-ant-…`, session cookies, tokens and passwords; diagnostics replace the key with `[set]` | `test_redaction`, `test_diagnostics_are_sanitized` |
| Browser session | anyone with `data/browser-profile` is logged in as you | git-ignored; keep the directory private (`chmod 700 data`); never share diagnostic bundles of it | operational |
| Access controls and challenges | automation bypasses login, 2FA, captchas, rate limits | detection only: the app pauses or opens the breaker; there is no code path that fills login forms or solves challenges | `test_session_expiry_detected_and_engine_pauses`, `test_rate_limit_opens_circuit_and_stops_platform_work` |
| Minors | contacting someone under the configured age | usernames with recent birth years skipped; stated ages stop contact; the model flags age signals | `test_intro_eligibility`, `test_underage_statement_stops_contact` |
| People who disengage | continued contact after "stop" | opt-out detection closes the conversation permanently and blocks re-introduction | `test_stop_request_is_respected_forever` |
| SQL injection | user data in SQL | all values are bound parameters; the only interpolated identifiers come from internal constants | review |

## Data handling

* Diagnostic bundles contain usernames (in errors, transitions and config). Treat them as personal data.
* Stored locally only: SQLite (`data/agent.db`), profile, browser profile, logs. Nothing is sent anywhere except
  model requests (conversation excerpt, relevant facts, memory) when `LLM__PROVIDER=anthropic`.
* Memory distinguishes `their_statement` (with the source message id), `interpretation` and `open_question`
  (source `model`, with confidence). Model interpretations are never promoted to facts.
* Retention: `Engine.apply_retention()` deletes memory and journal older than `POLICY__RETENTION_DAYS`.
  Per-person export: `GET /api/memory/{username}`. Deletion: `DELETE /api/memory/{username}`.

## Residual risks

* Running the Instagram adapter is automated use of Instagram Web, which Instagram's terms restrict. The approval
  gate keeps a human in control of every message, but account action by Instagram remains possible.
* The dashboard CSP allows inline script, which the single-file dashboard needs. Escaping is the XSS defence.
* Injection detection is heuristic; the structural defences (data framing, no tools, validation, review) carry
  the weight.
