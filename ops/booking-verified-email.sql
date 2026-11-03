CREATE TABLE IF NOT EXISTS booking_verified_emails (
    owner_hash TEXT NOT NULL,
    email_hash TEXT NOT NULL,
    email_encrypted TEXT NOT NULL,
    verified_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (owner_hash, email_hash)
);
CREATE INDEX IF NOT EXISTS booking_verified_email_owner ON booking_verified_emails(owner_hash, verified_at);
