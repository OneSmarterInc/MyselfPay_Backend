"""Committed, idempotent clinic notifications; SMTP acceptance is not inbox delivery."""
import os
import time
import booking_mail


def enabled():
    return os.getenv('BOOKING_EMAIL_ENABLED', 'false').lower() == 'true'


def recipient_allowed(recipient):
    allowed = {value.strip().casefold() for value in os.getenv('BOOKING_EMAIL_ALLOWED_RECIPIENTS', '').split(',') if value.strip()}
    return bool(recipient) and (not allowed or recipient.casefold() in allowed)


def mode(recipient):
    if not enabled():
        return 'suppressed'
    return 'enabled' if recipient_allowed(recipient) else 'unavailable'


def enqueue(con, ident):
    con.execute("INSERT INTO booking_outbox(request_id,state,created) VALUES(?,'queued',?) ON CONFLICT(request_id) DO NOTHING", (ident, time.time()))


def status(con, ident, fallback='suppressed'):
    # Legacy suppressed records are never replayed just because mail is enabled.
    row = con.execute('SELECT state FROM booking_outbox WHERE request_id=?', (ident,)).fetchone()
    return row['state'] if row else fallback


def deliver(path, ident):
    import booking
    now = time.time()
    with booking.connection(path, write=True) as con:
        row = booking.get_row(con, ident, lock=True)
        payload = booking.unpack(row)
        if mode(payload['recipient']) != 'enabled' or row['link_expires'] <= now or row['status'] in booking.TERMINAL:
            return False
        # A process can die after SMTP accepted DATA. Do not automatically replay
        # a stale sending record and risk duplicate center notifications.
        con.execute("UPDATE booking_outbox SET state='uncertain' WHERE request_id=? AND state='sending' AND last_attempt<?", (ident, now - 300))
        claimed = con.execute("UPDATE booking_outbox SET state='sending',attempts=attempts+1,last_attempt=? WHERE request_id=? AND state IN ('queued','failed') AND attempts<5 RETURNING request_id", (now, ident)).fetchone()
        if not claimed:
            return False
    try:
        booking_mail.send_message(booking.notification(payload, ident), payload['recipient'])
        outcome = 'sent'
    except booking_mail.MailDeliveryUncertain:
        outcome = 'uncertain'
    except booking_mail.MailUnavailable:
        outcome = 'failed'
    with booking.connection(path, write=True) as con:
        con.execute('UPDATE booking_outbox SET state=?,sent=? WHERE request_id=? AND state=?',
                    (outcome, time.time() if outcome == 'sent' else None, ident, 'sending'))
    return outcome == 'sent'
