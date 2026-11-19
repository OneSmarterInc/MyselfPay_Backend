-- Change record: allow the 'cancelled' request status (patient cancel, closed because the patient
-- scheduled elsewhere). Apply to the hosted PostgreSQL database only after a verified backup and
-- Vikram's approval. Local development databases pick this up from booking-schema.sql.
-- Verified effect: widens a CHECK constraint. No rows change.
BEGIN;
ALTER TABLE booking_requests DROP CONSTRAINT IF EXISTS booking_requests_status_check;
ALTER TABLE booking_requests ADD CONSTRAINT booking_requests_status_check
  CHECK (status IN ('requested','voicemail','scheduled','unable','expired','cancelled'));
COMMIT;
