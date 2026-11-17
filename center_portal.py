"""Email-owned center access, separate from patient and admin sessions."""
import hmac
import json
import secrets
import time
from decimal import Decimal
import booking
import booking_mail
import booking_chat
import core
from directory_policy import visible_sql


def centers(db, email):
    found = []
    predicate, values = visible_sql()
    for row in db.execute('SELECT entry_key,payload FROM entries WHERE batch_id=? AND '+predicate, (core.active_batch(db),*values)).fetchall():
        item = json.loads(row['payload'])
        if str(item.get('email', '')).strip().lower() == email:
            found.append({'id': row['entry_key'], **{key: item.get(key, '') for key in
                ('name', 'area', 'states', 'address', 'phone', 'email', 'source', 'coverage', 'entity', 'mri', 'ct', 'other', 'is_test_center')}})
    return found


def previous_emails(db, email):
    """Earlier login emails of this center, newest first, so open requests follow an email change."""
    found, current = [], email
    for _ in range(10):
        row = db.execute('SELECT old_cipher FROM center_email_history WHERE new_hash=? ORDER BY changed DESC LIMIT 1', (booking.digest(current),)).fetchone()
        if not row:
            break
        current = booking.cipher().decrypt(row['old_cipher'].encode()).decode()
        found.append(current)
    return found


def live_prices(db, keys):
    """Prices now published for these listings, so the center can see and change them."""
    out = []
    if not keys:
        return out
    marks = ','.join('?' * len(keys))
    for r in db.execute('SELECT id,entry_key,amount_cents,payload FROM offers WHERE batch_id=? AND amount_cents IS NOT NULL AND entry_key IN (' + marks + ')',
                        (core.active_batch(db), *keys)).fetchall():
        p = json.loads(r['payload'])
        if p.get('mod') not in ('MRI', 'CT') or p.get('type') not in ('fixed', 'starting'):
            continue
        out.append({'id': r['id'], 'entry_key': r['entry_key'], 'exam': p['mod'], 'amount_cents': r['amount_cents'],
                    'price_type': 'from' if p['type'] == 'starting' else 'fixed',
                    'contrast': 'either' if p.get('contrast') == 'both' else p.get('contrast', 'unknown'),
                    'interpretation_included': p.get('interpretation_included', 'unknown'),
                    'confirmed': p.get('confirmed_by_center', ''), 'source': p.get('source', '')})
    return out


def issue(path, address):
    email = str(address).strip().lower()
    if len(email) > 254 or not booking.EMAIL.fullmatch(email):
        raise booking.BookingError('Enter a valid center email address.')
    with booking.connection(path, write=True) as db:
        if not centers(db, email):
            raise booking.BookingError('Your email is not listed in our center directory. Please contact support.', 404)
        ident, code = secrets.token_urlsafe(32), f'{secrets.randbelow(1000000):06d}'
        db.execute('DELETE FROM center_login_checks WHERE expires<?', (time.time(),))
        db.execute('INSERT INTO center_login_checks(id,email_hash,email_cipher,code_hash,expires) VALUES(?,?,?,?,?)',
                   (ident, booking.digest(email), booking.cipher().encrypt(email.encode()).decode(), booking.digest(ident + ':' + code), time.time() + 600))
    try:
        booking_mail.send_center_code(email, code)
    except booking_mail.MailUnavailable as exc:
        with booking.connection(path, write=True) as db:
            db.execute('DELETE FROM center_login_checks WHERE id=?', (ident,))
        raise booking.BookingError(str(exc), 503) from None
    return {'verification_id': ident, 'expires_in': 600}


def verify(path, ident, code):
    failure = None
    with booking.connection(path, write=True) as db:
        sql = 'SELECT * FROM center_login_checks WHERE id=?'
        if not isinstance(db, __import__('sqlite3').Connection):
            sql += ' FOR UPDATE'
        row = db.execute(sql, (ident,)).fetchone()
        if not row or not row['code_hash'] or row['expires'] <= time.time():
            raise booking.BookingError('Code expired or already used. Request a new code.', 410)
        if row['attempts'] >= 5:
            raise booking.BookingError('Too many incorrect codes. Request a new code.', 429)
        if not hmac.compare_digest(booking.digest(ident + ':' + str(code)), row['code_hash']):
            db.execute('UPDATE center_login_checks SET attempts=attempts+1 WHERE id=?', (ident,))
            failure = booking.BookingError('Incorrect code. Please try again.')
        else:
            email = booking.cipher().decrypt(row['email_cipher'].encode()).decode()
            if not centers(db, email):
                raise booking.BookingError('This email is no longer listed. Please contact support.', 403)
            token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            db.execute('UPDATE center_login_checks SET code_hash=NULL WHERE id=?', (ident,))
            db.execute('DELETE FROM center_portal_sessions WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO center_portal_sessions(token_hash,email_cipher,csrf,expires) VALUES(?,?,?,?)',
                       (booking.digest(token), row['email_cipher'], csrf, time.time() + 28800))
    if failure:
        raise failure
    return token, csrf


def identity(db, token, csrf=None):
    row = db.execute('SELECT * FROM center_portal_sessions WHERE token_hash=?', (booking.digest(token),)).fetchone()
    if not row or row['expires'] <= time.time():
        raise booking.BookingError('Your center session has expired. Log in again.', 401)
    if csrf is not None and not hmac.compare_digest(str(csrf), row['csrf']):
        raise booking.BookingError('Please refresh your center dashboard and try again.', 403)
    email = booking.cipher().decrypt(row['email_cipher'].encode()).decode()
    listings = centers(db, email)
    if not listings:
        raise booking.BookingError('This email is no longer listed. Please contact support.', 403)
    return email, listings, row['csrf']


def dashboard(path, token):
    import center_profile
    with booking.connection(path) as db:
        email, listings, csrf = identity(db, token)
        keys = {item['id'] for item in listings}
        owned = [email, *previous_emails(db, email)]
        hashes = [booking.digest(x) for x in owned]
        rows = db.execute('SELECT b.* FROM booking_requests b JOIN center_request_routes r ON r.request_id=b.id WHERE r.email_hash IN (' + ','.join('?' * len(hashes)) + ') ORDER BY b.created DESC', hashes).fetchall()
        requests = []
        for row in rows:
            p = booking.unpack(row)
            if p['clinic_id'] not in keys or p['recipient'].strip().lower() not in owned:
                continue
            if (row['closed'] or row['link_expires']) + 30 * 86400 <= time.time():
                continue
            item = booking.receipt(row, db)
            item.pop('tracking_token', None)
            if row['status'] not in booking.TERMINAL and row['link_expires'] <= time.time():
                item['status'] = 'expired'
            requests.append(item)
        prices = [dict(r) for r in db.execute(
            'SELECT id,entry_key,exam,amount_cents,price_type,contrast,interpretation_included,note,submitted,status,decided FROM center_price_submissions '
            'WHERE email_hash IN (' + ','.join('?' * len(hashes)) + ') ORDER BY submitted DESC LIMIT 200', hashes).fetchall()]
        contact_history = [{'field': r['field'], 'old_value': r['old_value'], 'new_value': r['new_value'], 'changed': r['changed'], 'entry_keys': json.loads(r['entry_keys'])}
                           for r in db.execute('SELECT field,old_value,new_value,entry_keys,changed FROM center_contact_changes ORDER BY changed DESC LIMIT 500').fetchall()
                           if keys & set(json.loads(r['entry_keys']))][:100]
        return {'email': email, 'centers': listings, 'requests': requests, 'center_csrf': csrf, 'price_submissions': prices,
                'live_prices': live_prices(db, sorted(keys)), 'contact_history': contact_history,
                'profile_edits': center_profile.listing(db, hashes)}


def open_request(path, token, csrf, ident, status=None, appointment=None):
    with booking.connection(path, write=True) as db:
        email, listings, _ = identity(db, token, csrf)
        row = booking.get_row(db, ident, lock=True)
        p = booking.unpack(row)
        if p['clinic_id'] not in {item['id'] for item in listings} or p['recipient'].strip().lower() not in [email, *previous_emails(db, email)]:
            raise booking.BookingError('Request unavailable.', 404)
        if status is not None:
            if row['link_expires'] <= time.time():
                raise booking.BookingError('This request has expired.', 410)
            import appointment_slots
            row = appointment_slots.update(db, row, status, appointment)
            booking_chat.enqueue(db, row)
        result = booking.receipt(row, db)
        result.pop('tracking_token', None)
        if row['status'] not in booking.TERMINAL and row['link_expires'] > time.time():
            result.update(first_name=p['first_name'], phone=p['phone'])
        else:
            result['contact_hidden'] = True
            if row['status'] not in booking.TERMINAL:
                result['status'] = 'expired'
        return result


def submit_prices(path, token, csrf, entry_key, prices, consent):
    """Store center-entered prices as pending. They never change the published directory."""
    if consent is not True:
        raise booking.BookingError('Confirm that these are your current self-pay prices.')
    with booking.connection(path, write=True) as db:
        email, listings, _ = identity(db, token, csrf)
        if entry_key not in {item['id'] for item in listings}:
            raise booking.BookingError('Listing unavailable.', 404)
        now, saved = time.time(), []
        for item in prices:
            cents = int((Decimal(str(item['amount'])) * 100).to_integral_value())
            db.execute("UPDATE center_price_submissions SET status='replaced',decided=? WHERE entry_key=? AND exam=? AND status='pending'",
                       (now, entry_key, item['exam']))
            ident = secrets.token_urlsafe(12)
            db.execute('INSERT INTO center_price_submissions(id,entry_key,email_hash,exam,amount_cents,price_type,contrast,interpretation_included,note,submitted) '
                       'VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (ident, entry_key, booking.digest(email), item['exam'], cents, item['price_type'],
                        item['contrast'], item['interpretation_included'], item.get('note', '').strip(), now))
            saved.append({'id': ident, 'exam': item['exam'], 'amount_cents': cents, 'status': 'pending'})
        return {'saved': saved, 'message': 'Thank you. Your prices will appear after review.'}
