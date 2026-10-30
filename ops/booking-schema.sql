CREATE TABLE IF NOT EXISTS booking_requests (
    id TEXT PRIMARY KEY,
    owner_hash TEXT NOT NULL,
    idempotency_hash TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    payload TEXT NOT NULL,
    patient_hash TEXT NOT NULL,
    clinic_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('requested','voicemail','scheduled','unable','expired')),
    delivery TEXT NOT NULL CHECK (delivery IN ('suppressed')),
    created DOUBLE PRECISION NOT NULL,
    link_expires DOUBLE PRECISION NOT NULL,
    closed DOUBLE PRECISION,
    otp_hash TEXT,
    otp_expires DOUBLE PRECISION,
    otp_attempts INTEGER NOT NULL DEFAULT 0,
    otp_sent DOUBLE PRECISION,
    clinic_session_hash TEXT,
    clinic_session_expires DOUBLE PRECISION,
    UNIQUE (owner_hash, idempotency_hash)
);
CREATE INDEX IF NOT EXISTS booking_retention ON booking_requests (closed, link_expires);
CREATE INDEX IF NOT EXISTS booking_owner_created ON booking_requests (owner_hash, created);

CREATE TABLE IF NOT EXISTS booking_email_checks (
    id TEXT PRIMARY KEY,
    owner_hash TEXT NOT NULL,
    email_hash TEXT NOT NULL,
    email_encrypted TEXT NOT NULL,
    code_hash TEXT,
    proof_hash TEXT,
    state TEXT NOT NULL CHECK (state IN ('sending','sent','failed','verified','consumed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    created DOUBLE PRECISION NOT NULL,
    last_sent DOUBLE PRECISION NOT NULL,
    expires DOUBLE PRECISION NOT NULL,
    UNIQUE(owner_hash,email_hash)
);
CREATE INDEX IF NOT EXISTS booking_email_expiry ON booking_email_checks (expires);
CREATE TABLE IF NOT EXISTS booking_mail_limits (
    key TEXT NOT NULL,
    window_id BIGINT NOT NULL,
    attempts INTEGER NOT NULL,
    PRIMARY KEY(key,window_id)
);

