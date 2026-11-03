CREATE TABLE IF NOT EXISTS center_login_checks (
 id TEXT PRIMARY KEY, email_hash TEXT NOT NULL, email_cipher TEXT NOT NULL,
 code_hash TEXT, expires DOUBLE PRECISION NOT NULL, attempts INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS center_portal_sessions (
 token_hash TEXT PRIMARY KEY, email_cipher TEXT NOT NULL, csrf TEXT NOT NULL,
 expires DOUBLE PRECISION NOT NULL
);
CREATE TABLE IF NOT EXISTS center_request_routes (
 request_id TEXT PRIMARY KEY REFERENCES booking_requests(id) ON DELETE CASCADE,
 clinic_id TEXT NOT NULL, email_hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS center_routes_email ON center_request_routes(email_hash, clinic_id);
