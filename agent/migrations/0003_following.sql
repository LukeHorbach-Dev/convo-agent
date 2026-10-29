-- Whether you follow the account (read from your Following list) and when the agent unfollowed it for you.
ALTER TABLE accounts ADD COLUMN i_follow TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE accounts ADD COLUMN unfollowed_at TEXT
