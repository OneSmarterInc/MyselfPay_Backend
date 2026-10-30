CREATE TABLE IF NOT EXISTS booking_outbox (
    request_id TEXT PRIMARY KEY REFERENCES booking_requests(id) ON DELETE CASCADE,
    state TEXT NOT NULL CHECK (state IN ('queued','sending','sent','failed','uncertain')),
    attempts INTEGER NOT NULL DEFAULT 0,
    created DOUBLE PRECISION NOT NULL,
    last_attempt DOUBLE PRECISION,
    sent DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS booking_outbox_pending ON booking_outbox(state,created);
