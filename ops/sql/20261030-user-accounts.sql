-- Additive migration for an existing SelfPay database. No existing rows changed.
-- Run transactionally with psql -1 -v ON_ERROR_STOP=1.
CREATE TABLE IF NOT EXISTS user_accounts (
    member_id TEXT PRIMARY KEY REFERENCES members(id),
    email TEXT UNIQUE NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    created DOUBLE PRECISION NOT NULL
);

