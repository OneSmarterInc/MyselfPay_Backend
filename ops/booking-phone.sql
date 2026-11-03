CREATE TABLE IF NOT EXISTS booking_phone_checks (
 id TEXT PRIMARY KEY, owner_hash TEXT NOT NULL, phone_hash TEXT NOT NULL,
 phone_encrypted TEXT NOT NULL, provider_sid TEXT, proof_hash TEXT,
 state TEXT NOT NULL CHECK(state IN ('sending','sent','checking','failed','verified','consumed')),
 attempts INTEGER NOT NULL DEFAULT 0, created DOUBLE PRECISION NOT NULL,
 last_sent DOUBLE PRECISION NOT NULL, expires DOUBLE PRECISION NOT NULL,
 UNIQUE(owner_hash,phone_hash)
);
CREATE INDEX IF NOT EXISTS booking_phone_expiry ON booking_phone_checks(expires);
CREATE TABLE IF NOT EXISTS booking_sms_limits (
 key TEXT NOT NULL, window_id BIGINT NOT NULL, attempts INTEGER NOT NULL,
 PRIMARY KEY(key,window_id)
);
