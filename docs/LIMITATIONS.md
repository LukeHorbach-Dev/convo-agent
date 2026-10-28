# Known limitations

## External capabilities that don't exist

* **No new-follower event stream on Instagram Web.** Followers are inferred by diffing the followers list
  (`source = inferred`), limited to what loads within `max_scrolls`. Someone who follows and unfollows between polls
  is never seen. The first observation is a baseline.
* **No stable message ids on Instagram Web.** Messages get content-hash ids with an occurrence counter
  (`id_source = inferred`). Identical repeated messages are told apart by order only.
* **No idempotency key or authoritative delivery query.** Delivery checks re-read the thread
  (`delivery_check = best_effort`), so unknown outcomes go to you instead of being retried. Exactly-once delivery
  is not possible.
* **Conversation identity on Instagram** is read from links in the thread header and is only as reliable as that
  markup. Ambiguity fails closed (`ambiguous_state`).

## Implemented but not verified

* **The Instagram site profile (`InstagramProfile`) has never run against instagram.com.** Its locators are
  semantic best guesses (roles, visible text). The automated suite can't test it, and there is no Instagram
  session in this environment. Expect selector drift on first use; the app will detect it, open the breaker, and
  tell you which locator failed.
* **The Anthropic provider was not called live** (no API key here). It uses documented parameters (structured JSON
  output, server-side fallbacks) and maps SDK errors to categories; the suite runs on `FakeLLM`.
* **`python -m agent login`** is interactive and was not exercised.

## Simulated

* All autonomous end-to-end behaviour, delivery faults, crashes, outages and rate limits are verified against
  `MockPlatform` and the mock website (`mock_web`, with a real Chromium).
* Process crashes are simulated in-process (a `BaseException` that bypasses every handler, then a fresh engine on
  the same database). The kill-and-restart path was also exercised with the real CLI (SIGTERM and restart).

## Untested edge cases

See "Coverage is not proof" in [REVIEW.md](REVIEW.md): multi-process concurrency, real preemption timing between
the watchdog and worker threads, disk-full or power-loss, clock jumps, Instagram message requests and group
threads, edited or unsent messages, non-English Instagram UI text, and live-model draft quality.

## Design limits

* One worker thread, so throughput is one platform action at a time. That's intentional for a single browser
  session.
* Retrieval of personal facts is keyword-based (stopwords plus prefix matching), which is adequate for a
  profile of tens of facts.
* Reply drafts need a model; with `LLM__PROVIDER=fake` replies are canned and only suitable for tests.
* The daily send limit is a rolling 24 h count of sent messages, not a per-hour pacing scheme. There are no
  randomized pauses: timing tricks to avoid platform detection are deliberately out of scope.
* An imported list of 1,126 people at the default 25 per day takes about 45 days.
