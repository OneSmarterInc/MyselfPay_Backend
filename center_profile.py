"""Center-entered profile information (address, website). Changes show to patients only after an admin approves them."""
import json
import re
import secrets
import time
from urllib.parse import urlparse
import booking
import center_contact
import center_portal
import core

FIELDS = {'address': 'Address', 'website': 'Website'}
HOUR_LIMIT = 20


class ProfileError(ValueError):
    pass


def clean(field, value):
    value = ' '.join(str(value).split())
    if field == 'address':
        if len(value) < 5 or len(value) > 200:
            raise booking.BookingError('Enter the full street address, up to 200 characters.')
        return value
    if field == 'website':
        parsed = urlparse(value)
        if parsed.scheme != 'https' or not parsed.hostname or '.' not in parsed.hostname or parsed.username or parsed.password or len(value) > 300 or re.search(r'\s', value):
            raise booking.BookingError('Enter a website address that starts with https://')
        return value
    raise booking.BookingError('This field cannot be changed here.')


def submit(path, token, csrf, entry_key, field, value):
    value = clean(field, value)
    now = time.time()
    with booking.connection(path, write=True) as db:
        email, listings, _ = center_portal.identity(db, token, csrf)
        if entry_key not in {item['id'] for item in listings}:
            raise booking.BookingError('Listing unavailable.', 404)
        digest = booking.digest(email)
        if db.execute('SELECT COUNT(*) AS n FROM center_profile_edits WHERE email_hash=? AND submitted>?', (digest, now - 3600)).fetchone()['n'] >= HOUR_LIMIT:
            raise booking.BookingError('Too many changes. Try again in an hour.', 429)
        current = next(item for item in listings if item['id'] == entry_key)
        current_value = current.get('source' if field == 'website' else 'address', '')
        if value == current_value:
            raise booking.BookingError('That is already on your listing.')
        db.execute("UPDATE center_profile_edits SET status='replaced' WHERE entry_key=? AND field=? AND status='pending'", (entry_key, field))
        ident = secrets.token_urlsafe(12)
        db.execute('INSERT INTO center_profile_edits(id,entry_key,email_hash,field,value,old_value,submitted) VALUES(?,?,?,?,?,?,?)',
                   (ident, entry_key, digest, field, value, current_value, now))
    import admin_alerts
    admin_alerts.notify('profile', current.get('name', entry_key), f'{FIELDS[field]}: {value}')
    return {'saved': {'id': ident, 'entry_key': entry_key, 'field': field, 'value': value, 'status': 'pending'},
            'message': f'{FIELDS[field]} sent for review. Patients see it after approval.'}


def listing(db, hashes):
    marks = ','.join('?' * len(hashes))
    return [dict(r) for r in db.execute('SELECT id,entry_key,field,value,submitted,status,decided FROM center_profile_edits '
                                        'WHERE email_hash IN (' + marks + ') ORDER BY submitted DESC LIMIT 200', hashes).fetchall()]


def pending(path, status='pending'):
    with core.connect(path) as db:
        rows = db.execute('SELECT id,entry_key,field,value,old_value,submitted,status,decided,decided_by FROM center_profile_edits '
                          'WHERE status=? ORDER BY submitted DESC LIMIT 200', (status,)).fetchall()
        active = core.active_batch(db)
        out = []
        for r in rows:
            e = db.execute('SELECT payload FROM entries WHERE batch_id=? AND entry_key=?', (active, r['entry_key'])).fetchone()
            out.append({**dict(r), 'center': json.loads(e['payload']).get('name', r['entry_key']) if e else r['entry_key'], 'label': FIELDS.get(r['field'], r['field'])})
        return out


def reject(path, ident, by):
    if not by.strip():
        raise ProfileError('Reviewer name is required.')
    with core.connect(path) as db:
        if not db.execute("UPDATE center_profile_edits SET status='rejected',decided=?,decided_by=? WHERE id=? AND status='pending'",
                          (time.time(), by.strip(), ident)).rowcount:
            raise ProfileError('No pending change with that id.')


def approve(path, ident, by):
    if not by.strip():
        raise ProfileError('Reviewer name is required.')
    with core.connect(path) as db:
        row = db.execute("SELECT * FROM center_profile_edits WHERE id=? AND status='pending'", (ident,)).fetchone()
        if not row:
            raise ProfileError('No pending change with that id.')
        active = core.active_batch(db)
        entry = db.execute('SELECT payload FROM entries WHERE batch_id=? AND entry_key=?', (active, row['entry_key'])).fetchone()
        if not entry:
            raise ProfileError('This listing is not in the published directory.')
        payload = json.loads(entry['payload'])
        if row['field'] == 'website':
            payload['source'] = row['value']
        else:
            payload['address'] = row['value']
            payload['address_needs_geocode'] = True
        batch, previous = center_contact._clone_batch(db, 'profile-' + ident, {row['entry_key']: payload},
                                                       {'center_profile': row['field'], 'approved_by': by.strip(), 'entry': row['entry_key']})
        db.execute("UPDATE center_profile_edits SET status='approved',decided=?,decided_by=? WHERE id=?", (time.time(), by.strip(), ident))
    return {'batch': batch, 'previous_batch': previous}


def confirm_price(path, token, csrf, offer_id):
    """The center confirms a published price as it stands. The value does not change, so no review is needed."""
    from datetime import date
    with booking.connection(path) as db:
        _, listings, _ = center_portal.identity(db, token, csrf)
        owned = {item['id'] for item in listings}
    with core.connect(path) as db:
        active = core.active_batch(db)
        row = db.execute('SELECT id,entry_key,payload FROM offers WHERE batch_id=? AND id=?', (active, offer_id)).fetchone()
        if not row or row['entry_key'] not in owned:
            raise booking.BookingError('Price unavailable.', 404)
        offer = json.loads(row['payload'])
        if offer.get('confirmed_by_center'):
            raise booking.BookingError('This price is already confirmed.')
        today = date.today().isoformat()
        offer['confirmed_by_center'] = today
        entry = db.execute('SELECT payload FROM entries WHERE batch_id=? AND entry_key=?', (active, row['entry_key'])).fetchone()
        batch, previous = center_contact._clone_batch(db, 'confirm-' + offer_id, {row['entry_key']: json.loads(entry['payload'])},
                                                       {'center_confirm': offer_id, 'entry': row['entry_key']})
        db.execute('UPDATE offers SET payload=? WHERE batch_id=? AND id=?', (core.canonical(offer), batch, offer_id))
    return {'confirmed': offer_id, 'date': today, 'message': 'Thank you. Your price is marked as confirmed by you, ' + today + '.'}
