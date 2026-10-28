# Implementation report

Location: `convo-agent/` (standalone; no code or runtime dependency on the Chrome extension).
Size: about 3,800 lines of application code and 1,750 lines of tests.
Release review: [docs/REVIEW.md](docs/REVIEW.md), with 14 findings (3 high), all fixed with regression tests.

**Status legend:** **Tested** = implemented and covered by passing automated tests. **Simulated** = verified only
against the mock platform or mock site. **Partial** = implemented but not exercised end to end. **Unavailable** =
blocked by an external limitation.

## Acceptance criteria

| # | Criterion | Status | Evidence |
|---|---|---|---|
| 1 | Independent application | **Tested** | own `pyproject.toml`, `requirements.lock`, SQLite DB, config, tests; fresh-venv install verified |
| 2 | Real browser diagnostics | **Tested** (mock site) / **Partial** (Instagram) | `BrowserSupervisor`: health probe, bounded waits, redirect, stale-context, login/challenge detection, bounded restart; 14 real-Chromium tests |
| 3 | Follower and message monitoring with provenance | **Tested** | every observation tagged `verified`/`inferred`; intro_status never `sent` without confirmation; `unknown` blocks re-introduction |
| 4 | Conversation intelligence with stored evidence | **Tested** (with FakeLLM) / **Partial** (live model) | drafts store cited facts, context and validation; uncited claims, unverified or private facts force review |
| 5 | Durable processing | **Tested** | attempt committed before every send; dedup keys and unique constraints; bounded retries; reconciliation |
| 6 | Recovery | **Tested** | skip, pause, stop, circuit breaker, emergency stop, restart recovery, watchdog |
| 7 | Real-platform safeguards | **Tested** | approval enforced in code and not configurable; bound to recipient and text by hash, checked inside the send transaction; expires after 24 h; cancelled on opt-out, never-list or newer messages; mock_web can't point at a real site; challenges and rate limits are only detected, never worked around |
| 8 | Mock environment, autonomous | **Tested** | `python -m agent demo` (6 invariants), 36 fault tests, 40 to 300 chaos seeds |
| 9 | Dashboard | **Tested** | all listed panels and controls; screenshot in `docs/dashboard.png`; 11 API tests; live run verified with curl |
| 10 | Testing | **Tested** | 218 passing from a clean install (`docs/evidence/clean-install.txt`): unit, integration, end to end (direct and real Chromium), fault injection, chaos (300 seeds) |
| 11 | Honest report | this document | |

## Feature status

| Feature | Status | Notes |
|---|---|---|
| Job, outbox and browser state machines with persisted transitions | Tested | illegal transitions raise |
| Durable queue: dedup, CAS claims, leases, per-account serialization, backpressure | Tested | concurrent claims on separate connections |
| Transactional outbox and delivery attempts | Tested | not attempted / sent / failed / unknown / cancelled are distinct |
| Reconciliation of unknown deliveries | Tested | authoritative check (mock) can allow a retry; best effort (web) escalates to you |
| Restart recovery | Tested | in-process crash simulation plus real SIGTERM/restart of the CLI |
| Watchdog (progress, state time, total time) | Tested | separate thread; expires leases |
| Error classification and decision table | Tested | all 12 categories; see docs/RECOVERY.md |
| Circuit breaker (distinct accounts, cooldown, half-open) | Tested | |
| Emergency stop, pause, resume, stop | Tested | persists across restarts (verified live) |
| Daily send limit | Tested | rolling 24 h |
| Personality profile with fact provenance | Tested | `data/profile.json` (yours), fixtures for tests |
| Draft validation (facts, style, policy, injection) | Tested | |
| Disengagement and age stops | Tested | |
| Memory with provenance, export, delete, retention | Tested | retention runs daily |
| Research journal (observed vs interpreted) | Tested (writes) | records sends and opt-outs; richer observations depend on a live model |
| Structured JSON logs with redaction, metrics, health endpoints, diagnostics bundle | Tested | |
| Dashboard auth, CSRF, DNS-rebinding protection | Tested | |
| Mock platform (direct) with fault injection | Tested | |
| Mock website plus browser adapter | Tested | real Chromium 153 |
| Inferred-follower baseline | Tested | prevents introducing every existing follower on first run |
| Anthropic LLM provider | **Partial** | written against the SDK in use; no API key here, so never called live |
| Instagram site profile (locators) | **Partial** | implemented, never run against instagram.com; expect selector drift on first use, which the app detects |
| `agent login` (manual sign-in helper) | **Partial** | interactive; not exercised |
| New-follower events on Instagram | **Unavailable** | no event stream; inferred by list diffing |
| Stable message ids on Instagram | **Unavailable** | content-hash ids instead (`inferred`) |
| Authoritative delivery confirmation on Instagram | **Unavailable** | best effort; unknowns go to you |
| Exactly-once delivery | **Unavailable** | no idempotency key; at-most-once with human review |
| Never-message list (`data/never.txt`) | Tested | no intro, no reply; cancels already-approved messages |
| List import (`import-list`) | Tested | same eligibility, age, never-list, limit and approval rules |
| Plain-English translation, language, reply meaning, read aloud, text size | Tested (storage, display, XSS) / **Partial** (translation quality needs the live model) | |
| Autonomous sending on Instagram | **Not built, by design** | every real-platform message requires your approval |
| Randomized pauses to avoid detection | **Not built, by design** | timing evasion is out of scope; the daily limit stays |

## Commands

```bash
cd convo-agent
uv venv -p 3.12 .venv
uv pip install -p .venv/bin/python -r requirements.lock
uv pip install -p .venv/bin/python -e . --no-deps

.venv/bin/python -m pytest                                  # 218 tests, about 1 min
CHAOS_SEEDS=300 .venv/bin/python -m pytest tests/test_chaos.py
.venv/bin/python -m agent demo                              # mock end-to-end with faults
.venv/bin/python -m agent run --platform mock_web           # dashboard at http://127.0.0.1:8765/, token in data/dashboard-token
.venv/bin/python -m agent login                             # then:
AGENT_PLATFORM=instagram .venv/bin/python -m agent run      # approval required per message
```

## Release decision

See [docs/REVIEW.md](docs/REVIEW.md#decision). Mock and mock_web: ready. Instagram: ready only for a supervised
trial in draft mode, then execute with approval. Not for unattended use.

## Unresolved risks

1. **Instagram locators are unverified.** The first real run will probably hit selector drift. It's detected
   and explained, but locators will need updating in `agent/platform/sites.py`.
2. **Instagram's terms restrict automation.** The approval gate keeps you in control of every message, but the
   account could still be actioned by Instagram for automated browsing.
3. **Best-effort reconciliation looks for the exact text.** An identical earlier message can make an undelivered
   one look delivered. That's biased toward not resending, deliberately.
4. **Live-model quality is unmeasured.** Validation catches invented facts and rule breaks, but tone and
   helpfulness with the real model haven't been evaluated.
