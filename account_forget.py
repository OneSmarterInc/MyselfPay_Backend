"""Forget me and the 30-day account cleanup. Both remove the same records as an admin deletion."""
import hashlib
import sqlite3
import time
import booking
import booking_phone
import core
import mobile_accounts
from admin_accounts import BOOKING_TABLES, MEMBER_TABLES

RETENTION = 30 * 86400


def owner(member_id):
    return 'forget:' + member_id


def issue(path, member_id):
    """Text a code to the number on the account, so only its owner can delete it."""
    with core.connect(path) as db:
        row = db.execute('SELECT phone FROM mobile_accounts WHERE member_id=?', (member_id,)).fetchone()
    if not row:
        raise booking.BookingError('Account unavailable.', 404)
    return booking_phone.request_code(path, owner(member_id), row['phone'])


def table_names(db):
    if isinstance(db, sqlite3.Connection):
        rows = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    else:
        rows = db.execute('SELECT table_name AS name FROM information_schema.tables WHERE table_schema=current_schema()').fetchall()
    return {r['name'] for r in rows}


def delete_member(db, member_id):
    """Remove a member and everything keyed to them. The caller owns the transaction."""
    digest = hashlib.sha256(member_id.encode()).hexdigest()
    names = table_names(db)

    def has(table):
        return table in names
    for table in BOOKING_TABLES:
        if has(table):
            db.execute(f'DELETE FROM {table} WHERE owner_hash=?', (digest,))
    row = db.execute('SELECT phone FROM mobile_accounts WHERE member_id=?', (member_id,)).fetchone() if has('mobile_accounts') else None
    if row and has('booking_phone_checks'):
        db.execute('DELETE FROM booking_phone_checks WHERE phone_hash=?', (booking.digest(row['phone']),))
    for table in MEMBER_TABLES:
        if has(table):
            db.execute(f'DELETE FROM {table} WHERE member_id=?', (member_id,))
    for key in ('account-disabled:' + member_id, 'booking-phone-owner:' + digest, 'booking-email-owner:' + digest):
        db.execute('DELETE FROM settings WHERE key=?', (key,))
    db.execute('DELETE FROM members WHERE id=?', (member_id,))


def forget(path, member_id, ident, code):
    """Confirm the texted code, cancel open requests, tell the facilities and delete the account."""
    booking_phone.verify_code(path, owner(member_id), ident, code)
    mine = booking.digest(member_id)
    now = time.time()
    with booking.connection(path, write=True) as db:
        if not db.execute('SELECT 1 FROM mobile_accounts WHERE member_id=?', (member_id,)).fetchone():
            raise booking.BookingError('Account unavailable.', 404)
        told = [row['id'] for row in db.execute(
            "SELECT id FROM booking_requests WHERE owner_hash=? AND status IN ('requested','voicemail','scheduled') AND link_expires>?",
            (mine, now)).fetchall()]
    # Facilities hear first, while the request still exists.
    for ident_ in told:
        booking.notify_center_closed(path, ident_, 'patient_cancelled')
    with booking.connection(path, write=True) as db:
        delete_member(db, member_id)
    return {'deleted': True, 'requests_cancelled': len(told)}


def purge_inactive(path, now=None):
    """Delete accounts whose last request closed 30 or more days ago. Returns how many."""
    now = now or time.time()
    removed = 0
    with booking.connection(path, write=True) as db:
        done = {r['owner_hash'] for r in db.execute('SELECT owner_hash FROM booking_requests WHERE closed IS NOT NULL AND closed<=?', (now - RETENTION,)).fetchall()}
        busy = {r['owner_hash'] for r in db.execute('SELECT owner_hash FROM booking_requests WHERE closed IS NULL OR closed>?', (now - RETENTION,)).fetchall()}
        stale = done - busy
        if not stale:
            return 0
        for r in db.execute("SELECT id FROM members WHERE tenant='selfpay'").fetchall():
            if hashlib.sha256(r['id'].encode()).hexdigest() in stale:
                delete_member(db, r['id'])
                removed += 1
    return removed
