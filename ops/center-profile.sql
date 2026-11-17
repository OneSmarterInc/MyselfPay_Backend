CREATE TABLE IF NOT EXISTS center_profile_edits (
 id TEXT PRIMARY KEY, entry_key TEXT NOT NULL, email_hash TEXT NOT NULL, field TEXT NOT NULL,
 value TEXT NOT NULL, old_value TEXT, submitted DOUBLE PRECISION NOT NULL,
 status TEXT NOT NULL DEFAULT 'pending', decided DOUBLE PRECISION, decided_by TEXT
);
CREATE INDEX IF NOT EXISTS center_profile_entry ON center_profile_edits(entry_key, field, status);
