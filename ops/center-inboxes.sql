CREATE TABLE IF NOT EXISTS center_inbox_checks (
 id TEXT PRIMARY KEY, session_hash TEXT NOT NULL, inbox_cipher TEXT NOT NULL, code_hash TEXT NOT NULL,
 expires DOUBLE PRECISION NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, created DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS center_inbox_checks_session ON center_inbox_checks(session_hash, created);
CREATE TABLE IF NOT EXISTS center_inboxes (
 email_hash TEXT PRIMARY KEY, inbox_cipher TEXT NOT NULL, verified DOUBLE PRECISION NOT NULL
);
