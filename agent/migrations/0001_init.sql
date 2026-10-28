-- Initial schema. Every entity has a stable internal id and timestamps (UTC ISO-8601).

CREATE TABLE accounts (
    id               TEXT PRIMARY KEY,
    platform_id      TEXT,                       -- stable platform id when the platform exposes one
    username         TEXT NOT NULL UNIQUE,
    display_name     TEXT,
    profile_snapshot TEXT,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    follower_status  TEXT NOT NULL DEFAULT 'unknown',   -- follower | not_follower | unknown
    follower_source  TEXT,                               -- verified | inferred
    intro_status     TEXT NOT NULL DEFAULT 'none',      -- none | pending | sent | unknown | skipped
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    opted_out        INTEGER NOT NULL DEFAULT 0,
    review_status    TEXT NOT NULL DEFAULT 'ok',        -- ok | needs_review | quarantined
    review_reason    TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
CREATE UNIQUE INDEX accounts_platform_id ON accounts(platform_id) WHERE platform_id IS NOT NULL;

CREATE TABLE follower_events (
    id           TEXT PRIMARY KEY,
    dedup_key    TEXT NOT NULL UNIQUE,
    username     TEXT NOT NULL,
    source       TEXT NOT NULL,          -- verified | inferred
    observed_at  TEXT NOT NULL,
    payload      TEXT
);

CREATE TABLE conversations (
    id                    TEXT PRIMARY KEY,
    thread_key            TEXT NOT NULL UNIQUE,
    account_id            TEXT REFERENCES accounts(id),
    identity_source       TEXT NOT NULL DEFAULT 'verified',
    status                TEXT NOT NULL DEFAULT 'active',  -- active | opted_out | needs_review
    last_processed_msg_id TEXT,
    summary               TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);

CREATE TABLE messages (
    id                  TEXT PRIMARY KEY,
    conversation_id     TEXT NOT NULL REFERENCES conversations(id),
    platform_message_id TEXT NOT NULL,
    id_source           TEXT NOT NULL DEFAULT 'verified',  -- verified | inferred (content hash)
    direction           TEXT NOT NULL,                     -- in | out
    text                TEXT NOT NULL,
    sent_at             TEXT,
    observed_at         TEXT NOT NULL,
    UNIQUE (conversation_id, platform_message_id)
);

CREATE TABLE jobs (
    id              TEXT PRIMARY KEY,
    kind            TEXT NOT NULL,              -- intro | reply
    subject_key     TEXT NOT NULL,              -- serialization key (account username / thread)
    dedup_key       TEXT NOT NULL UNIQUE,
    state           TEXT NOT NULL,
    state_since     TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    retries_by_category TEXT NOT NULL DEFAULT '{}',
    last_error      TEXT,
    error_category  TEXT,
    lease_owner     TEXT,
    lease_expires_at TEXT,
    heartbeat_at    TEXT,
    progress_at     TEXT,
    started_at      TEXT,
    run_after       TEXT,
    payload         TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);
CREATE INDEX jobs_state ON jobs(state, run_after);
CREATE INDEX jobs_subject ON jobs(subject_key);

CREATE TABLE job_transitions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id     TEXT NOT NULL REFERENCES jobs(id),
    from_state TEXT,
    to_state   TEXT NOT NULL,
    reason     TEXT NOT NULL,
    at         TEXT NOT NULL
);

CREATE TABLE drafts (
    id            TEXT PRIMARY KEY,
    job_id        TEXT NOT NULL REFERENCES jobs(id),
    target        TEXT NOT NULL,              -- username or thread key
    text          TEXT NOT NULL,
    intent        TEXT,
    rationale     TEXT,
    facts_used    TEXT NOT NULL DEFAULT '[]', -- [{id, text, source}]
    context       TEXT NOT NULL DEFAULT '{}', -- history excerpt and memory used
    validation    TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected | superseded
    decided_by    TEXT,
    decided_at    TEXT,
    created_at    TEXT NOT NULL
);

CREATE TABLE outbox (
    id                  TEXT PRIMARY KEY,         -- operation id
    job_id              TEXT NOT NULL UNIQUE REFERENCES jobs(id),
    draft_id            TEXT NOT NULL REFERENCES drafts(id),
    target              TEXT NOT NULL,
    text                TEXT NOT NULL,
    state               TEXT NOT NULL,            -- pending | attempting | sent | failed | unknown | cancelled
    approved_by         TEXT,                     -- 'operator' or 'policy:mock'
    approved_at         TEXT,
    platform_message_id TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE delivery_attempts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    outbox_id   TEXT NOT NULL REFERENCES outbox(id),
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    result      TEXT,                         -- sent | failed | unknown (NULL = interrupted)
    error       TEXT
);

CREATE TABLE memory (
    id          TEXT PRIMARY KEY,
    target      TEXT NOT NULL,                -- username
    kind        TEXT NOT NULL,                -- their_statement | my_fact | model_summary | interpretation | open_question
    content     TEXT NOT NULL,
    source      TEXT NOT NULL,                -- message id, 'operator', or 'model'
    confidence  REAL,
    created_at  TEXT NOT NULL
);
CREATE INDEX memory_target ON memory(target);

CREATE TABLE journal (
    id          TEXT PRIMARY KEY,
    target      TEXT,
    observation TEXT NOT NULL,
    basis       TEXT NOT NULL,                -- observed | interpreted
    uncertainty TEXT,
    created_at  TEXT NOT NULL
);

CREATE TABLE recovery_events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    at        TEXT NOT NULL,
    scope     TEXT NOT NULL,                  -- job | account | application | platform
    job_id    TEXT,
    subject   TEXT,
    category  TEXT NOT NULL,
    decision  TEXT NOT NULL,
    detail    TEXT
);

CREATE TABLE audit (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    at      TEXT NOT NULL,
    actor   TEXT NOT NULL,
    action  TEXT NOT NULL,
    detail  TEXT
);

CREATE TABLE app_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
