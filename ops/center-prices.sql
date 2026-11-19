CREATE TABLE IF NOT EXISTS center_price_submissions (
 id TEXT PRIMARY KEY, entry_key TEXT NOT NULL, email_hash TEXT NOT NULL,
 exam TEXT NOT NULL, amount_cents INTEGER NOT NULL, price_type TEXT NOT NULL,
 contrast TEXT NOT NULL, interpretation_included TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
 submitted DOUBLE PRECISION NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
 decided DOUBLE PRECISION, decided_by TEXT
);
CREATE INDEX IF NOT EXISTS center_price_entry ON center_price_submissions(entry_key, exam, status);
