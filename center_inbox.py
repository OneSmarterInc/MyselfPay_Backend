"""Where a center's patient requests are delivered. A test email with a code must be answered first.

The login email stays the owner of every request. A verified request inbox only changes where the
notification mail goes, and only for requests made after it was verified. Removing it returns delivery
to the listing email.
"""
import hmac
import secrets
import time
import booking
import booking_delivery
import booking_mail
import center_portal

SEND_LIMIT = 5      # test messages per session per hour
CODE_SECONDS = 600


def start(path, token, csrf, address):
    now = time.time()
    inbox = str(address).strip().lower()
    if len(inbox) > 254 or not booking.EMAIL.fullmatch(inbox):
        raise booking.BookingError('Enter a valid email address.')
    with booking.connection(path, write=True) as db:
        email, _, _ = center_portal.identity(db, token, csrf)
        if inbox == email:
            raise booking.BookingError('That is already your login email. Remove the other inbox to use it.')
        if booking_delivery.mode(inbox) == 'unavailable':
            raise booking.BookingError('That address cannot receive requests yet. Contact support.', 409)
        session = booking.digest(token)
        db.execute('DELETE FROM center_inbox_checks WHERE created<?', (now - 3600,))
        if db.execute('SELECT COUNT(*) AS n FROM center_inbox_checks WHERE session_hash=?', (session,)).fetchone()['n'] >= SEND_LIMIT:
            raise booking.BookingError('Too many test messages. Try again in an hour.', 429)
        ident, code = secrets.token_urlsafe(24), f'{secrets.randbelow(1000000):06d}'
        db.execute('INSERT INTO center_inbox_checks(id,session_hash,inbox_cipher,code_hash,expires,created) VALUES(?,?,?,?,?,?)',
                   (ident, session, booking.cipher().encrypt(inbox.encode()).decode(), booking.digest(ident + ':' + code), now + CODE_SECONDS, now))
    try:
        booking_mail.send_center_inbox_test(inbox, code, email)
    except booking_mail.MailUnavailable as exc:
        with booking.connection(path, write=True) as db:
            db.execute('DELETE FROM center_inbox_checks WHERE id=?', (ident,))
        raise booking.BookingError(str(exc), 503) from None
    return {'verification_id': ident, 'expires_in': CODE_SECONDS, 'sent_to': inbox}


def confirm(path, token, csrf, ident, code):
    now = time.time()
    failure = None
    with booking.connection(path, write=True) as db:
        email, _, _ = center_portal.identity(db, token, csrf)
        sql = 'SELECT * FROM center_inbox_checks WHERE id=? AND session_hash=?'
        if not isinstance(db, __import__('sqlite3').Connection):
            sql += ' FOR UPDATE'
        row = db.execute(sql, (ident, booking.digest(token))).fetchone()
        if not row or row['expires'] <= now:
            raise booking.BookingError('Code expired or already used. Send a new test.', 410)
        if row['attempts'] >= 5:
            raise booking.BookingError('Too many incorrect codes. Send a new test.', 429)
        if not hmac.compare_digest(booking.digest(ident + ':' + code), row['code_hash']):
            db.execute('UPDATE center_inbox_checks SET attempts=attempts+1 WHERE id=?', (ident,))
            failure = booking.BookingError('Incorrect code. Please try again.')
        else:
            db.execute('INSERT INTO center_inboxes(email_hash,inbox_cipher,verified) VALUES(?,?,?) '
                       'ON CONFLICT(email_hash) DO UPDATE SET inbox_cipher=excluded.inbox_cipher,verified=excluded.verified',
                       (booking.digest(email), row['inbox_cipher'], now))
            db.execute('DELETE FROM center_inbox_checks WHERE id=?', (ident,))
            inbox = booking.cipher().decrypt(row['inbox_cipher'].encode()).decode()
            import audit
            audit.log('center.inbox.set', scope='', target_id=ident, actor=email, actor_type='center', new=inbox)
    if failure:
        raise failure
    return {'request_inbox': inbox}


def remove(path, token, csrf):
    with booking.connection(path, write=True) as db:
        email, _, _ = center_portal.identity(db, token, csrf)
        db.execute('DELETE FROM center_inboxes WHERE email_hash=?', (booking.digest(email),))
        import audit
        audit.log('center.inbox.removed', scope='', target_id='', actor=email, actor_type='center')
    return {'request_inbox': ''}
