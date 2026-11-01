CREATE TABLE IF NOT EXISTS account_zip_codes (
    member_id TEXT PRIMARY KEY REFERENCES members(id) ON DELETE CASCADE,
    zip_code TEXT NOT NULL
);
