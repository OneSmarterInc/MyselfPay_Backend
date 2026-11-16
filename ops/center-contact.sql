CREATE TABLE IF NOT EXISTS center_contact_checks (
 id TEXT PRIMARY KEY, session_hash TEXT NOT NULL, field TEXT NOT NULL, entry_key TEXT,
 new_cipher TEXT NOT NULL, code_hash TEXT, verify_sid TEXT,
 expires DOUBLE PRECISION NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, created DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS center_contact_session ON center_contact_checks(session_hash, created);
CREATE TABLE IF NOT EXISTS center_contact_changes (
 id TEXT PRIMARY KEY, field TEXT NOT NULL, old_value TEXT NOT NULL, new_value TEXT NOT NULL,
 entry_keys TEXT NOT NULL, batch TEXT NOT NULL, previous_batch TEXT NOT NULL, changed DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS center_email_history (
 new_hash TEXT NOT NULL, old_hash TEXT NOT NULL, old_cipher TEXT NOT NULL, changed DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS center_email_history_new ON center_email_history(new_hash);
