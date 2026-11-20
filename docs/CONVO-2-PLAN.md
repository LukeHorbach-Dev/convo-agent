# Convo 2.0: technical assessment and plan

Written 20 November 2026, before any Convo 2.0 code is changed (brief, section 14). Nothing below has been built yet.

---

## 1. What exists today (Convo 5.6.3-preview.1)

**Size:** about 8,000 lines of Python in `assistant/`, 4,700 lines of React in `web/src/`, a 1,000-line classic screen
(`assistant/web/app.html`), 4,900 lines in the older engine (`agent/`), 26 test files with 274 tests (366 test cases
in all), 110 API endpoints.

### How it fits together

```
Convo.app (Mac)  ->  mac/start.sh  ->  python -m assistant
                                        |
             FastAPI on 127.0.0.1:8765 (+ optional Tailscale address)
                                        |
   React Command Centre (web/)   Classic screens (app.html)   Native Mac window (pywebview)
                                        |
   SQLite (data/assistant.db, numbered migrations, backup before each)
   Files: data/library/ (posts), data/integrations/*.json (keys, owner-only), .env (settings, AI key)
                                        |
   Instagram website (Convo's own Chromium + Claude "eyes")   Instagram download files (JSON)
   YouTube / Instagram / TikTok official APIs (connect/)       Claude API (writing, Ask, strategist)
   GitHub releases (updates)
```

### What it does, and how real each part is

| Area | Status | Notes |
|---|---|---|
| Hellos and replies written in Luke's voice, approved, sent through the browser | **Fully implemented** | Used daily on the real account |
| Stop now, daily limits, quiet hours, AI spending limit, 6-hour stop on Instagram warnings | **Fully implemented** | |
| Unfollow (one person, ticked, everyone) | **Fully implemented** | Browser |
| People memory, notes, reports, never list, safety rules (under 19, money, addresses) | **Fully implemented** | |
| Ask (⌘K) and AI Strategist from real numbers | **Fully implemented** | Tools, not guesses |
| Follow-ups and reminders | **Fully implemented** | From downloads |
| Content Studio, Library, Calendar, Scheduled, AI captions | **Fully implemented** | Files stay on the Mac |
| Missions, goals, Organic Independence Score, launch mode, collaborations | **Fully implemented** | From real activity only |
| Updates from GitHub with undo, one-line install, self-repair | **Fully implemented** | |
| Phone access over Tailscale | **Implemented, not yet tried on Luke's phone** | |
| YouTube connection (stats, analytics, private upload) | **Simulated** | Tested against a pretend Google; needs Luke's Google app |
| Instagram API (stats, insights, Reels, comment replies) | **Simulated** | Tested against a pretend Meta; needs Creator account + Meta app |
| TikTok (stats, video list, drafts to inbox) | **Simulated** | Needs TikTok developer app (Sandbox) |
| Public publishing on YouTube and TikTok | **Awaiting platform approval** | Private/draft until Google and TikTok audit the apps |
| Instagram DMs through the API | **Not built yet** | Possible for Creator accounts (see section 3) |
| Starting DMs through any official API | **Unavailable** | No platform allows it; Hello Message keeps the browser method |
| TikTok messages and comments | **Unavailable** | TikTok's API has none |
| Facebook | **Not built yet** | Pages only (section 3) |

---

## 2. Findings

### Works well and should be kept as it is
- The safety model: code-level checks before anything is sent, approval first, Stop now, limits, under-19 and money rules.
- The database approach: numbered migrations with a full backup before each.
- The update system, installer and self-repair.
- The Content Studio, missions and goals logic, which already count only real activity.

### Duplicated or left over
1. **Two engines.** `agent/` is the version-1 engine. Convo still uses about half of it (the AI client, profile and
   voice, safety rules, browser and site profiles, errors, settings classes). The other half (`agent/engine`,
   `agent/web`, `agent/app.py`, `demo.py`, `core/db`, `jobs`, `recovery`, `states`) isn't used by Convo, but 9 test
   files still test it. **Plan:** move the used half into `assistant/core/`, then retire the rest and its tests.
2. **Two sets of screens.** The classic screens still hold a few things the new design doesn't: the live view
   ("watch it work"), Help, and practice mode's start button. **Plan:** move those across, then keep classic for one
   release as a fallback and remove it.
3. **"Roles" instead of workspaces.** Accounts are `main`, `launch` or `other`. That's close to what the brief
   wants, but there's no proper separation.

### Gaps against the brief
1. **One connection per platform.** Keys are stored per platform (`data/integrations/youtube.json`), so only **one**
   YouTube, Instagram and TikTok account can be connected. 100+ accounts needs keys **per account**.
2. **Conversations aren't tied to an account.** `people`, `threads`, `messages`, `drafts` and `unfollows` assume
   one Instagram account. A second inbox needs an `account_id` on each.
3. **Keys aren't encrypted.** They're in owner-only files (`.env`, `data/integrations/*.json`). Fine for one Mac,
   but the brief asks for encryption. **Plan:** the macOS Keychain.
4. **Stop now doesn't cover everything.** It stops sending and writing, but not an upload already running, the
   6-hourly platform syncs, or scheduled reminders.
5. **No audit log.** The activity feed is plain text. There's no record of who (you, the AI, a workflow) did what,
   to which account, and what it looked like before.
6. **Messages depend on downloads.** Conversations, followers and following come from Instagram's download files.
   The official API can now cover Instagram DMs for Creator accounts, though not who you follow.
7. **No approval centre.** Hellos, replies, comment replies and posts each have their own approval place.
8. **No automation engine.** The "automations" today are fixed features with switches.

### Changes that could break things (and how to avoid that)
| Change | Risk | How it's made safe |
|---|---|---|
| Adding `workspace_id` and `account_id` to existing tables | Old rows without an account | Migration fills them in with your personal Instagram account, with a backup first; tests on a copy of a 1,886-hello database |
| Moving keys into the Keychain | Platforms disconnect | Copy first, check they work, then delete the old file; Go back still works |
| New navigation | Old links and bookmarks | Every old address redirects to its new place |
| Retiring `agent/` and classic screens | Losing something that still works | Only after the new design has every feature, with tests showing it |
| Per-account connections | The current YouTube/Instagram/TikTok link | Moved over automatically as "your personal account on that platform" |

---

## 3. What the platforms allow (this sets the limits)

| | Instagram (Creator/Business) | Facebook | TikTok | YouTube |
|---|---|---|---|---|
| Followers, profile numbers | Yes | Pages only | Yes | Yes |
| Views, reach, saves, shares per post | Yes | Pages only | Views, likes, comments, shares | Views, likes, comments |
| Watch time and retention | Reels only, partly | Pages video | No | Yes (with retention curves) |
| Where viewers came from | No | Partly | No | Yes |
| Read DMs | **Yes** (people who wrote to you) | Pages (Messenger) | **No** | YouTube has no DMs |
| Reply to DMs | Yes, within 24 hours of their last message | Pages, same 24-hour rule | No | — |
| Start a DM | **No** | **No** | **No** | — |
| Comments: read and reply | Yes | Pages | **No** | Read yes; reply needs a wider permission |
| Publish | Photos, carousels, Reels (photos need a public link) | Pages | Drafts to your inbox; direct posts private until audited | Private until Google audits the app |
| Personal profile | Professional accounts only | **No API for personal profiles** | Yes | Yes |
| Many accounts | Each must be added to your Meta app until it passes App Review and Business Verification | Same | Sandbox: your own few accounts; more needs TikTok's review | Up to 100 users before Google verification; ~6 uploads a day per Google project |

**What this means for Convo 2.0:**
- **Facebook:** supported for **Pages**, not a personal profile.
- **Supporting Network:** works for a handful of your own accounts straight away. Going to 100+ means passing each
  platform's review, which needs forms and sometimes business documents from you. The architecture will be ready
  for it either way.
- **Hello Message:** stays as it is: browser-based, your personal account only, approval first. Browser automation
  will **not** be extended to other accounts. It's the riskiest part, and the APIs cover the rest.

---

## 4. Proposed architecture

### Data
- **New `workspaces` table:** Personal Brand and Supporting Network (more possible).
- **Every account belongs to one workspace.** Every post, goal, contact, conversation, comment and number belongs to
  one account, so to one workspace.
- **New `connections` table:** one row per account, holding status, permissions, last sync, last error and health.
  **Keys live in the macOS Keychain**, one entry per account (an owner-only file only where there's no Keychain, as in
  the test machine).
- **New `audit` table:** time, who (you, AI, workflow, system), what, which account, a before-and-after summary.
  Nothing is ever deleted from it.
- **New `approvals` table:** one list for everything waiting on you. It *points to* the existing drafts, comments
  and posts, so nothing is copied twice.
- **New `events`, `workflows` and `workflow_runs` tables:** for the automation engine.
- **New `sync_jobs` table:** per account and per kind of data: next run, attempts, last error.

### Guardrails against posting to the wrong account
- **One clear target:** every outward action names exactly one account, checked on the server.
- **Confirmation shows the target:** every "Publish" or "Send" box shows the account's picture, handle, platform and
  workspace.
- **Combined view is read-only:** it can't publish.
- **Workspace in the URL:** a page left open can't quietly switch workspace.

### Engines
1. **Sync engine:**
   - Schedules updates per account, retries with back-off, and doesn't save duplicates.
   - Records "this platform doesn't provide this" so screens can say so plainly.
   - Downloads stay as a fallback and for following/followers.
2. **Event and automation engine:**
   - Triggers: new message, new comment, new upload, schedule, account disconnected, goal reached.
   - Conditions: platform, workspace, category, keywords, time.
   - Actions: categorise, draft a reply, write captions, make a report, notify, add to approvals.
   - **Anything that sends or posts always becomes an approval, never runs by itself.**
   - Each run is logged step by step, with retries and error handling.
3. **Central assistant:**
   - Today's Ask/Strategist, extended to know workspaces, accounts, voices and content.
   - New tools: summarise inbox, prepare replies, plan next week, best posts, explain performance, accounts needing
     attention.
   - Every answer names its sources.
   - Every action it proposes goes to approvals.
4. **Emergency stop:** one switch stops sending, writing, publishing, syncing and workflows, everywhere, at once.

### Screens (six sections + Settings)
| Section | What moves there |
|---|---|
| **Home** | Daily briefing: outstanding messages, pending approvals, upcoming posts, connection problems, recent results, follow-ups, suggested priorities; ask the assistant |
| **Accounts** | Workspace switcher, account list and groups, a profile page per account, connections and health, Supporting Network, launch mode |
| **Inbox** | Conversations (DMs from download + Instagram API), comments, hellos (outreach), contacts (CRM), follow-ups, collaborations, saved replies, labels and priorities |
| **Content** | Studio, Library, Calendar, Scheduled, publishing status |
| **Intelligence** | Analytics per account and combined, platform comparison, best posts, retention, goals, missions, independence score, strategist, reports |
| **Automation** | Workflow builder, runs and history, audit log, emergency stop |
| **Approvals** | A badge in the top bar opens the Approval Centre from anywhere |
| **Settings** | AI and voices (one voice per brand), keys, phone access, updates, backups and recovery |

**Also:**
- The command palette (⌘K) grows to jump to any page, account or contact, and to run actions.
- Keyboard shortcuts for approve, skip, next and search.
- The **workflow builder** is a clear step list (When, If, Then), not a free-form canvas. It's easier to read,
  works on a phone and with a keyboard, and does the same job.

---

## 5. Phased plan

Each phase ends with: all tests passing, a preview on GitHub, and a report to you (done, works, what needs you) before
it's called finished.

| Phase | What | Why this order |
|---|---|---|
| **0** | This assessment | Asked for first |
| **1. Foundations** | Workspaces; account IDs on every table (with a backup); per-account connections; Keychain; audit log; emergency stop covering everything; move the used half of `agent/` and retire the rest | Everything else builds on it; no visible change except the workspace switcher |
| **2. Navigation and Home** | Six sections with redirects; Home briefing; account profile pages; command palette and shortcuts; Approvals badge | Makes the rest easy to find |
| **3. Approval Centre** | One list for messages, comment replies, posts, AI suggestions, account changes and workflow actions; approve, edit, reject, postpone; full history | Automation and the assistant need it |
| **4. Inbox and CRM** | Instagram DMs through the API (Creator account), comments from Instagram, YouTube and Facebook Pages, labels, priorities, AI categories, saved replies, search and filters | Biggest daily time-saver |
| **5. Sync engine and Intelligence** | Scheduled sync with retries and health; Facebook Pages; YouTube retention; platform comparison; combined analytics; strategist with sources | Less reliance on downloads |
| **6. Automation builder** | Triggers, conditions, actions; the four example workflows as ready-made templates; runs, retries, logs | Uses everything above |
| **7. Content studio, advanced** | Subtitles (on your Mac, free), thumbnails from your frames, photo publishing (temporary links), publishing status per platform | Builds on stable publishing |
| **8. Video editing** | Long video to Shorts/TikToks: pick moments, cut, reframe, caption | Largest single piece; last so it doesn't hold the rest up |
| **9. Finish** | Remove classic screens; full regression and real-browser tests; feature status page inside Convo (implemented / simulated / awaiting approval / unavailable); install, recovery and user guides | Definition of completion |

Rough size: phases 1 to 6 are the core of Convo 2.0. Phases 7 and 8 are the content-creation extras.

---

## 6. Decisions needed from you before phase 1

1. **Facebook:** Pages only (the API has nothing for personal profiles). OK?
2. **Supporting Network accounts:** are they all yours, or do some belong to other people or businesses? Either works,
   but other people's accounts need their sign-in and, past a few accounts, each platform's review (forms from you).
3. **Instagram DMs through the API:** use it for your personal account once it's a Creator account (inbox updates by
   itself, replies within 24 hours), keeping downloads for history and for who you follow?
4. **Encryption:** keys in the macOS Keychain (recommended). Should the database itself be encrypted too? FileVault,
   which encrypts the whole Mac, already covers it with no extra steps, so I'd suggest relying on that.
5. **Retiring the old parts:** remove the classic screens and the unused half of the old engine once everything has
   a home in the new design?
6. **Automation:** confirm the rule that anything that sends or posts always waits for your approval, even inside a
   workflow.
7. **Current preview first:** finish checking 5.6.x on your Mac and release it as 6.0.0 before phase 1 starts? (It
   gives a clean, known-good point to come back to.)
