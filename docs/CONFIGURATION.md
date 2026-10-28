# Configuration

Settings come from environment variables (or `.env`) with the prefix `AGENT_`; nested keys use `__`
(`AGENT_LIMITS__MAX_ATTEMPTS=4`). Validation happens at startup; invalid values stop the app with a clear error.

| Setting | Default | Meaning |
|---|---|---|
| `PLATFORM` | `mock` | `mock` (in-process), `mock_web` (real Chromium + local mock site), `instagram` |
| `MODE` | `simulate` | `observe` (ingest only), `draft` (never sends), `simulate` (mock only), `execute` |
| `ACCOUNT_USERNAME` | `lukehorbach2` | expected logged-in account; a mismatch pauses the app |
| `PROFILE_PATH` | `data/profile.json` | personality profile |
| `DB_PATH` | `data/agent.db` | SQLite database |
| `DATA_DIR` | `data` | token, lock, logs |
| `POLL_INTERVAL_S` | 30 | scheduler tick |
| `MOCK_SITE_URL` | `http://127.0.0.1:8899` | mock site for `mock_web`; must be a loopback http URL (validated) |
| `BROWSER__USER_DATA_DIR` | `data/browser-profile` | persistent Chrome profile (holds your session; keep private) |
| `BROWSER__HEADLESS` | false | show the browser window |
| `BROWSER__EXECUTABLE_PATH` | none | use an installed Chrome instead of Playwright's Chromium |
| `BROWSER__NAVIGATION_TIMEOUT_MS` | 20000 | per navigation |
| `BROWSER__ACTION_TIMEOUT_MS` | 8000 | per element wait/action |
| `BROWSER__HEALTH_CHECK_TIMEOUT_MS` | 5000 | liveness probe |
| `BROWSER__MAX_RECOVERIES_PER_RUN` | 3 | browser restarts before pausing |
| `LLM__PROVIDER` | `fake` | `fake` or `anthropic` |
| `LLM__MODEL` | `claude-opus-5-5` | model id |
| `LLM__API_KEY` | none | or `ANTHROPIC_API_KEY` |
| `LLM__TIMEOUT_S` | 45 | per request; SDK retries disabled (recovery owns retries) |
| `LIMITS__MAX_ATTEMPTS` | 4 | per job |
| `LIMITS__MAX_RETRIES_PER_CATEGORY` | 3 | per job and error category |
| `LIMITS__BACKOFF_BASE_S` / `BACKOFF_MAX_S` | 2 / 300 | exponential backoff with jitter |
| `LIMITS__LEASE_S` | 120 | job lease |
| `LIMITS__STALL_S` | 180 | no-progress threshold (watchdog) |
| `LIMITS__MAX_STATE_S` | 600 | max time in one state |
| `LIMITS__MAX_JOB_S` | 900 | max total job time |
| `LIMITS__QUEUE_CAPACITY` | 5000 | backpressure threshold |
| `LIMITS__CONSECUTIVE_ACCOUNT_FAILURES` | 2 | selector/ambiguity failures before an account is skipped |
| `LIMITS__DAILY_SEND_LIMIT` | 25 | sends per rolling 24 h |
| `CIRCUIT__FAILURE_THRESHOLD` | 5 | distinct accounts failing the same way |
| `CIRCUIT__WINDOW_S` / `COOLDOWN_S` | 600 / 900 | breaker window and cooldown |
| `DASHBOARD__HOST` / `PORT` | 127.0.0.1 / 8765 | non-localhost hosts are rejected by validation |
| `POLICY__MOCK_REQUIRES_APPROVAL` | false | approval on mock platforms (real platforms always require it) |
| `POLICY__INTROS_ENABLED` / `REPLIES_ENABLED` | true / true | feature flags |
| `POLICY__INTRO_EXISTING_FOLLOWERS` | false | introduce yourself to people who followed before the first run |
| `POLICY__MIN_AGE` | 19 | stated ages below this (or above 90) stop contact |
| `POLICY__EXCLUDED_USERNAMES` | [] | JSON list, never contacted (no intro, no reply) |
| `POLICY__NEVER_LIST_PATH` | `data/never.txt` | file, one username per line, merged into the list above at startup |
| `POLICY__APPROVAL_TTL_HOURS` | 24 | an operator approval older than this goes back for review |
| `POLICY__RETENTION_DAYS` | 180 | memory and journal retention |

## Profile (`data/profile.json`)

* `facts[]`: `id`, `text`, `topic`, `source` (`user` / `writing_sample` / `model`), `verified`, `shareable`.
  Only `source=user, verified, shareable` facts may back a claim in a draft.
* `writing_samples[]`: your real messages; the model imitates them.
* `style`: summary, rules, vocabulary, `max_words`.
* `boundaries[]`, `needs_approval_topics[]`: topics that force human review.
* `intro_templates[]`: the only texts used for introductions.
