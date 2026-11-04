CREATE TABLE IF NOT EXISTS booking_chat_events (
 request_id TEXT NOT NULL REFERENCES booking_requests(id) ON DELETE CASCADE,
 event_id TEXT NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 next_attempt DOUBLE PRECISION NOT NULL DEFAULT 0, lease TEXT,
 created DOUBLE PRECISION NOT NULL,
 PRIMARY KEY (request_id, event_id)
);
CREATE INDEX IF NOT EXISTS booking_chat_events_due ON booking_chat_events(state, next_attempt);
