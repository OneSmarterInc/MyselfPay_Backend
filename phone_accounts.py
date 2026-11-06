"""Passwordless patient authentication using purpose-bound, single-use SMS OTPs."""
import secrets
import sqlite3
import time
import uuid
import accounts
import booking
import booking_phone
import core
import mobile_accounts
import us_phone


def intent(data):
    value = data.get('intent')
    if value not in ('register', 'login', 'request'):
        raise accounts.AccountError('Choose a valid mobile verification flow.')
    return value


def challenge_owner(data):
    token = data.get('challenge')
    # Reuse the existing strict opaque challenge validation, with a separate purpose.
    mobile_accounts.owner(token)
    return 'mobile-auth:' + intent(data) + ':' + token


def first_name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 100 or any(ord(c) < 32 for c in value):
        raise accounts.AccountError('Enter your first name (1–100 characters).')
    return value.strip()


def identity(db, phone):
    row = db.execute('SELECT m.id FROM members m JOIN mobile_accounts a ON a.member_id=m.id WHERE a.phone=?', (phone,)).fetchone()
    if row:
        return row['id'], False
    # Preserve existing accounts and their request history when a unique saved phone matches.
    rows = mobile_accounts.legacy_candidates(db, phone)
    if len(rows) > 1:
        raise accounts.AccountError('More than one account uses this number. Contact support.', 409)
    return (rows[0]['id'], True) if rows else ('', False)


def issue(path, data):
    action = intent(data)
    phone = us_phone.normalize(data.get('phone')) if action == 'request' else booking_phone.phone_number(data.get('phone'))
    if action == 'register':
        first_name(data.get('name'))
    with core.connect(path) as db:
        ident, _ = identity(db, phone)
        if ident and mobile_accounts.disabled(db, ident):
            raise accounts.AccountError('Account unavailable. Contact support.', 403)
        if action == 'register' and ident:
            raise accounts.AccountError('This mobile number is already registered. Please log in.', 409)
        if action == 'login' and not ident:
            raise accounts.AccountError('This mobile number is not registered. Create an account first.', 404)
    challenge = secrets.token_urlsafe(32)
    result = booking_phone.request_code(path, challenge_owner({'intent':action, 'challenge':challenge}), phone)
    return {**result, 'challenge':challenge, **({'phone':phone} if action == 'request' else {})}


def finish(path, data, previous_token=''):
    action = intent(data)
    phone = us_phone.normalize(data.get('phone')) if action == 'request' else booking_phone.phone_number(data.get('phone'))
    name = first_name(data.get('name')) if action == 'register' else ''
    owner = challenge_owner(data)
    checked = booking_phone.verify_code(path, owner, data.get('verification_id'), data.get('code'))
    with booking.connection(path, write=True) as db:
        # Serialize account creation/legacy conversion and phone uniqueness on both databases.
        db.execute("INSERT INTO settings(key,value) VALUES('account-registration-lock','1') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        booking_phone.require_verified(db, booking.digest(owner), checked['verification_id'], checked['verification_token'], phone)
        ident, legacy = identity(db, phone)
        if ident and mobile_accounts.disabled(db, ident):
            raise accounts.AccountError('Account unavailable. Contact support.', 403)
        created = action == 'register' or (action == 'request' and not ident)
        if created:
            if ident:
                raise accounts.AccountError('This mobile number is already registered. Please log in.', 409)
            ident, salt = str(uuid.uuid4()), secrets.token_hex(16)
            internal = 'mobile:' + ident
            # Random inaccessible legacy credential; no password is collected or sent to the patient.
            db.execute('INSERT INTO members(id,tenant,email,name,salt,password_hash) VALUES(?,?,?,?,?,?)',
                       (ident, 'selfpay', internal, name, salt, 'otp-only$' + secrets.token_hex(32)))
            db.execute('INSERT INTO user_accounts(member_id,email,phone,created) VALUES(?,?,?,?)', (ident,internal,phone,time.time()))
            db.execute('INSERT INTO account_zip_codes(member_id,zip_code) VALUES(?,?)', (ident,''))
        elif not ident:
            raise accounts.AccountError('Account unavailable. Create an account first.', 404)
        if created or legacy:
            internal_name = 'otp:' + uuid.uuid4().hex
            db.execute('INSERT INTO mobile_accounts VALUES(?,?,?,?,?)', (ident,internal_name,internal_name,phone,time.time()))
            if legacy:
                db.execute('UPDATE members SET email=? WHERE id=?', ('mobile:'+ident,ident))
                db.execute('UPDATE user_accounts SET email=?,phone=? WHERE member_id=?', ('mobile:'+ident,phone,ident))
                db.execute('DELETE FROM sessions WHERE member_id=?', (ident,))
        # Lock the existing member against concurrent administrative deletion.
        sql = 'SELECT id FROM members WHERE id=?'
        if not isinstance(db, sqlite3.Connection):
            sql += ' FOR UPDATE'
        if not db.execute(sql,(ident,)).fetchone():
            raise accounts.AccountError('Account unavailable.',404)
        db.execute("UPDATE booking_phone_checks SET state='consumed',proof_hash=NULL WHERE id=?", (checked['verification_id'],))
        return accounts.new_session(db, ident, previous_token)
