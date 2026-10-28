-- Bind each approval to the exact recipient and text it was given for (sha256 of target + NUL + text).
ALTER TABLE outbox ADD COLUMN approved_hash TEXT;
