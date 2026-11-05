"""Username accounts with single-use Twilio registration proofs; no patient email."""
import hmac
import re
import secrets
import time
import uuid
import accounts
import booking
import booking_phone
import core

SCHEMA = '''CREATE TABLE IF NOT EXISTS mobile_accounts (
 member_id TEXT PRIMARY KEY REFERENCES members(id),
 username TEXT NOT NULL, username_key TEXT UNIQUE NOT NULL,
 phone TEXT UNIQUE NOT NULL, verified_at DOUBLE PRECISION NOT NULL
);'''


def initialize(path):
    with core.connect(path) as db:
        db.executescript(SCHEMA)


def username(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{2,29}', value.strip()):
        raise accounts.AccountError('Choose a username of 3–30 letters, numbers, dots, hyphens or underscores.')
    return value.strip()


def profile(db, member):
    member = dict(member)
    member.pop('email', None)
    row = db.execute('SELECT username,phone,verified_at FROM mobile_accounts WHERE member_id=?', (member['id'],)).fetchone()
    member.update(username=row['username'] if row and not row['username'].startswith('otp:') else '', phone=row['phone'] if row else '',
                  mobile_verified=bool(row), setup_required=not bool(row))
    return member


def owner(token):
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,100}', token):
        raise accounts.AccountError('Restart mobile verification.')
    return 'registration:' + token


def request_code(path, data, ip):
    phone = booking_phone.phone_number(data.get('phone'))
    token = secrets.token_urlsafe(32)
    result = booking_phone.request_code(path, owner(token), phone)
    return {**result, 'challenge': token}


def verify_code(path, data):
    return booking_phone.verify_code(path, owner(data.get('challenge')), data.get('verification_id'), data.get('code'))


def password_matches(row, password):
    salt = row['salt'] if row else '00' * 16
    candidate = 'scrypt-v1$' + accounts.password_hash(password, salt)
    return hmac.compare_digest(candidate, row['password_hash'] if row else 'scrypt-v1$' + '0' * 64)


def authenticate(path, data, action, ip, previous_token='', existing_member=None):
    name = username(data.get('username'))
    accounts.throttle(path, action, ip, name.casefold())
    password = data.get('password', '')
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        raise accounts.AccountError('Enter a password of no more than 128 characters.')
    if action == 'login':
        with booking.connection(path, write=True) as db:
            sql = 'SELECT m.* FROM members m JOIN mobile_accounts a ON a.member_id=m.id WHERE a.username_key=?'
            import sqlite3
            if not isinstance(db, sqlite3.Connection):
                sql += ' FOR UPDATE OF m'
            row = db.execute(sql, (name.casefold(),)).fetchone()
            if not password_matches(row, password) or disabled(db, row['id'] if row else ''):
                raise accounts.AccountError('Username or password is incorrect.', 401)
            return accounts.new_session(db, row['id'], previous_token)
    phone = booking_phone.phone_number(data.get('phone'))
    full_name, zip_code = data.get('name', ''), data.get('zip_code', '')
    if action == 'register' and (not isinstance(full_name, str) or not 1 <= len(full_name.strip()) <= 100 or any(ord(c) < 32 for c in full_name)):
        raise accounts.AccountError('Enter your name (1–100 characters).')
    if action == 'register' and len(password) < 12:
        raise accounts.AccountError('Use a password of 12–128 characters.')
    if not isinstance(zip_code, str) or (zip_code and not re.fullmatch(r'[0-9]{5}', zip_code)):
        raise accounts.AccountError('Enter a five-digit ZIP code, or leave it blank.')
    with booking.connection(path, write=True) as db:
        db.execute("INSERT INTO settings(key,value) VALUES('account-registration-lock','1') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        booking_phone.require_verified(db, booking.digest(owner(data.get('challenge'))), data.get('verification_id'), data.get('verification_token'), phone)
        if db.execute('SELECT member_id FROM mobile_accounts WHERE username_key=? OR phone=?', (name.casefold(), phone)).fetchone():
            raise accounts.AccountError('Username or mobile number is already registered. Try logging in or choose another username.', 409)
        if action == 'setup':
            if existing_member and existing_member.get('tenant') != 'guest':
                rows = db.execute('SELECT m.* FROM members m JOIN user_accounts a ON a.member_id=m.id WHERE m.id=?', (existing_member['id'],)).fetchall()
            else:
                # Existing patients prove both their saved mobile and their old password.
                rows = legacy_candidates(db, phone)
            matches = [r for r in rows if password_matches(r, password) and not disabled(db, r['id'])]
            if len(matches) != 1:
                raise accounts.AccountError('Unable to match your existing account. Use its saved mobile number and password, or contact support.', 401)
            ident = matches[0]['id']
            if db.execute('SELECT member_id FROM mobile_accounts WHERE member_id=?', (ident,)).fetchone():
                raise accounts.AccountError('This account is already set up. Log in using your username.', 409)
            db.execute('UPDATE user_accounts SET phone=? WHERE member_id=?', (phone, ident))
        else:
            if legacy_candidates(db, phone):
                raise accounts.AccountError('An existing account uses this mobile number. Choose Set up your existing account to keep your requests.', 409)
            ident, salt = str(uuid.uuid4()), secrets.token_hex(16)
            # Legacy NOT NULL unique identity columns retain opaque IDs, never patient emails.
            internal = 'mobile:' + ident
            db.execute('INSERT INTO members(id,tenant,email,name,salt,password_hash) VALUES(?,?,?,?,?,?)',
                       (ident, 'selfpay', internal, full_name.strip(), salt, 'scrypt-v1$' + accounts.password_hash(password, salt)))
            db.execute('INSERT INTO user_accounts(member_id,email,phone,created) VALUES(?,?,?,?)', (ident, internal, phone, time.time()))
            db.execute('INSERT INTO account_zip_codes(member_id,zip_code) VALUES(?,?)', (ident, zip_code))
        db.execute('INSERT INTO mobile_accounts VALUES(?,?,?,?,?)', (ident, name, name.casefold(), phone, time.time()))
        db.execute('UPDATE members SET email=? WHERE id=?', ('mobile:' + ident, ident))
        db.execute('UPDATE user_accounts SET email=? WHERE member_id=?', ('mobile:' + ident, ident))
        db.execute("UPDATE booking_phone_checks SET state='consumed',proof_hash=NULL WHERE id=?", (data['verification_id'],))
        # Revoke previous sessions; setup cannot leave old email-based sessions active.
        db.execute('DELETE FROM sessions WHERE member_id=?', (ident,))
        return accounts.new_session(db, ident, previous_token)


def disabled(db, ident):
    return bool(db.execute("SELECT value FROM settings WHERE key=? AND value='1'", ('account-disabled:' + ident,)).fetchone())


def normalize_saved(value):
    try:
        return booking_phone.phone_number(value)
    except booking.BookingError:
        return ''


def booking_phone_for(db, ident):
    row = db.execute('SELECT phone FROM mobile_accounts WHERE member_id=?', (ident,)).fetchone()
    if not row:
        raise booking.BookingError('Verify your mobile number before requesting a time.', 403)
    if disabled(db, ident):
        raise booking.BookingError('Account unavailable.', 403)
    return row['phone']


def legacy_candidates(db, phone):
    # Bounded password work: the stored mobile is matched in SQL before scrypt.
    compact = "REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(a.phone,' ',''),'(',''),')',''),'-',''),'.','')"
    return db.execute('SELECT m.* FROM members m JOIN user_accounts a ON a.member_id=m.id LEFT JOIN mobile_accounts n ON n.member_id=m.id WHERE n.member_id IS NULL AND '+compact+'=? LIMIT 2', (phone,)).fetchall()
