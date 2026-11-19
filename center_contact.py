"""Center contact changes. A code goes to the NEW email or phone; the listing changes only after it is entered.

An email change moves the login. The directory email of every listing that used the old email changes, and the
center signs in again with the new one. A phone change updates one listing. Each change keeps the old directory
batch, so it can be rolled back with core.publish(previous_batch).
"""
import hmac
import json
import secrets
import time
import uuid
import booking
import booking_mail
import booking_phone
import booking_sms
import center_portal
import core

SEND_LIMIT = 5      # codes per session per hour
CODE_SECONDS = 600


def _digits(value):
    return ''.join(ch for ch in str(value) if ch.isdigit())


def start(path, token, csrf, field, value, entry_key=None):
    now = time.time()
    with booking.connection(path, write=True) as db:
        email, listings, _ = center_portal.identity(db, token, csrf)
        session = booking.digest(token)
        db.execute('DELETE FROM center_contact_checks WHERE created<?', (now - 3600,))
        if db.execute('SELECT COUNT(*) AS n FROM center_contact_checks WHERE session_hash=?', (session,)).fetchone()['n'] >= SEND_LIMIT:
            raise booking.BookingError('Too many code requests. Try again in an hour.', 429)
        ident, code, sid_key = secrets.token_urlsafe(24), f'{secrets.randbelow(1000000):06d}', None
        if field == 'email':
            new = str(value).strip().lower()
            if len(new) > 254 or not booking.EMAIL.fullmatch(new):
                raise booking.BookingError('Enter a valid email address.')
            if new == email:
                raise booking.BookingError('That is already your login email.')
            if center_portal.centers(db, new):
                raise booking.BookingError('That email already belongs to another center listing. Contact support.', 409)
            code_hash = booking.digest(ident + ':' + code)
        else:
            if entry_key not in {item['id'] for item in listings}:
                raise booking.BookingError('Listing unavailable.', 404)
            new = booking_phone.phone_number(value)
            current = next(item for item in listings if item['id'] == entry_key)
            if _digits(new) == _digits(current.get('phone', '')):
                raise booking.BookingError('That is already the phone number on this listing.')
            code_hash = None
        db.execute('INSERT INTO center_contact_checks(id,session_hash,field,entry_key,new_cipher,code_hash,expires,created) VALUES(?,?,?,?,?,?,?,?)',
                   (ident, session, field, entry_key if field == 'phone' else None, booking.cipher().encrypt(new.encode()).decode(), code_hash, now + CODE_SECONDS, now))
    try:
        if field == 'email':
            booking_mail.send_center_contact_code(new, code, email)
        else:
            sid = booking_sms.send_code(new)
            with booking.connection(path, write=True) as db:
                db.execute('UPDATE center_contact_checks SET verify_sid=? WHERE id=?', (sid, ident))
    except (booking_mail.MailUnavailable, booking_sms.SMSUnavailable) as exc:
        with booking.connection(path, write=True) as db:
            db.execute('DELETE FROM center_contact_checks WHERE id=?', (ident,))
        raise booking.BookingError(str(exc), 503) from None
    return {'verification_id': ident, 'expires_in': CODE_SECONDS, 'field': field, 'sent_to': new}


def _clone_batch(db, ident, edits, report):
    """Publish a new directory batch where edited entries replace the active ones."""
    active = core.active_batch(db)
    batch = str(uuid.uuid4())
    db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)',
               (batch, core.digest({'parent': active, 'center_contact': ident}), 'Center contact ' + ident, time.time(), 'staged', '{}'))
    marks = ','.join('?' * len(edits))
    db.execute('INSERT INTO entries(batch_id,entry_key,content_hash,payload) SELECT ?,entry_key,content_hash,payload FROM entries '
               'WHERE batch_id=? AND entry_key NOT IN (' + marks + ')', (batch, active, *edits))
    for key, payload in edits.items():
        db.execute('INSERT INTO entries VALUES(?,?,?,?)', (batch, key, core.digest(payload), core.canonical(payload)))
    db.execute('INSERT INTO offers(batch_id,id,entry_key,amount_cents,max_cents,price_type,payload) '
               'SELECT ?,id,entry_key,amount_cents,max_cents,price_type,payload FROM offers WHERE batch_id=?', (batch, active))
    import spatial
    spatial.index_batch(db, batch)
    db.execute("UPDATE batches SET status='published',report=? WHERE id=?", (core.canonical({'parent_batch': active, **report}), batch))
    db.execute("INSERT INTO settings VALUES('active_batch',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (batch,))
    db.execute('INSERT INTO publish_events(batch_id,previous_batch,created,action) VALUES(?,?,?,?)', (batch, active, time.time(), 'publish'))
    return batch, active


def confirm(path, token, csrf, ident, code):
    now = time.time()
    with booking.connection(path, write=True) as db:
        center_portal.identity(db, token, csrf)
        sql = 'SELECT * FROM center_contact_checks WHERE id=? AND session_hash=?'
        if not isinstance(db, __import__('sqlite3').Connection):
            sql += ' FOR UPDATE'
        row = db.execute(sql, (ident, booking.digest(token))).fetchone()
        if not row or row['expires'] <= now:
            raise booking.BookingError('Code expired or already used. Request a new code.', 410)
        if row['attempts'] >= 5:
            raise booking.BookingError('Too many incorrect codes. Request a new code.', 429)
        db.execute('UPDATE center_contact_checks SET attempts=attempts+1 WHERE id=?', (ident,))
    if row['field'] == 'email':
        good = hmac.compare_digest(booking.digest(ident + ':' + str(code)), row['code_hash'] or '')
    else:
        try:
            good = bool(row['verify_sid']) and booking_sms.check_code(row['verify_sid'], str(code))
        except booking_sms.SMSUnavailable as exc:
            raise booking.BookingError(str(exc), 503) from None
    if not good:
        raise booking.BookingError('Incorrect code. Please try again.')
    new = booking.cipher().decrypt(row['new_cipher'].encode()).decode()
    with booking.connection(path, write=True) as db:
        email, listings, _ = center_portal.identity(db, token, csrf)
        active = core.active_batch(db)
        payloads = {r['entry_key']: json.loads(r['payload']) for r in db.execute('SELECT entry_key,payload FROM entries WHERE batch_id=?', (active,)).fetchall()}
        if row['field'] == 'email':
            if center_portal.centers(db, new):
                raise booking.BookingError('That email already belongs to another center listing. Contact support.', 409)
            keys = [item['id'] for item in listings]
            old = email
            edits = {k: {**payloads[k], 'email': new} for k in keys}
        else:
            if row['entry_key'] not in {item['id'] for item in listings}:
                raise booking.BookingError('Listing unavailable.', 404)
            keys = [row['entry_key']]
            old = str(payloads[row['entry_key']].get('phone', ''))
            edits = {row['entry_key']: {**payloads[row['entry_key']], 'phone': new}}
        batch, previous = _clone_batch(db, ident, edits, {'center_contact': row['field'], 'entries': keys})
        db.execute('INSERT INTO center_contact_changes VALUES(?,?,?,?,?,?,?,?)', (ident, row['field'], old, new, json.dumps(keys), batch, previous, now))
        db.execute('DELETE FROM center_contact_checks WHERE id=?', (ident,))
        import audit
        for key in keys:
            audit.log('center.contact.changed', scope=key, target_type=row['field'], target_id=ident, actor=email, actor_type='center',
                      old=old, new=new, detail={'batch': batch, 'previous_batch': previous})
        if row['field'] == 'email':
            db.execute('INSERT INTO center_email_history VALUES(?,?,?,?)', (booking.digest(new), booking.digest(old), booking.cipher().encrypt(old.encode()).decode(), now))
            db.execute('DELETE FROM center_portal_sessions WHERE token_hash=?', (booking.digest(token),))
    if row['field'] == 'email':
        try:
            booking_mail.send_center_contact_notice(old, new)
        except booking_mail.MailUnavailable:
            pass
        return {'changed': True, 'field': 'email', 'email': new, 'login_required': True,
                'message': 'Your login email is now ' + new + '. Sign in again with the new email.'}
    return {'changed': True, 'field': 'phone', 'phone': new, 'entry_key': row['entry_key'], 'message': 'Your phone number is updated.'}
