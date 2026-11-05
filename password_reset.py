"""Single-use, mobile-bound password recovery, separate from registration proofs."""
import hmac
import re
import secrets
import sqlite3
import time
import accounts
import booking
import booking_phone
import core
import mobile_accounts

SCHEMA = '''CREATE TABLE IF NOT EXISTS password_reset_requests (
 challenge_hash TEXT PRIMARY KEY,
 member_id TEXT NOT NULL REFERENCES members(id) ON DELETE CASCADE,
 verification_id TEXT NOT NULL,
 phone_hash TEXT NOT NULL,
 auth_stamp TEXT NOT NULL,
 expires DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS password_reset_expiry ON password_reset_requests(expires);'''
MESSAGE = 'If these details match an active account, an SMS code has been sent to its registered mobile number.'


def initialize(path):
    with core.connect(path) as db:
        db.executescript(SCHEMA)


def owner(token):
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{32,100}', token):
        raise accounts.AccountError('Reset verification expired. Request another code.', 410)
    return 'password-reset:' + token


def stamp(row):
    return booking.digest(row['salt'] + '\0' + row['password_hash'])


def issue(path, data):
    name = mobile_accounts.username(data.get('username'))
    phone = booking_phone.phone_number(data.get('phone'))
    challenge = secrets.token_urlsafe(32)
    with core.connect(path) as db:
        row = db.execute('SELECT m.id,m.salt,m.password_hash,a.phone FROM members m JOIN mobile_accounts a ON a.member_id=m.id JOIN user_accounts u ON u.member_id=m.id WHERE a.username_key=? AND a.phone=?', (name.casefold(), phone)).fetchone()
        if row and mobile_accounts.disabled(db, row['id']):
            row = None
    if not row:
        return {'challenge':challenge,'verification_id':secrets.token_urlsafe(24),'message':MESSAGE,'expires_in':600}
    issued = booking_phone.request_code(path, owner(challenge), row['phone'])
    with core.connect(path) as db:
        db.execute('DELETE FROM password_reset_requests WHERE expires<=?', (time.time(),))
        db.execute('INSERT INTO password_reset_requests VALUES(?,?,?,?,?,?)',
                   (booking.digest(challenge),row['id'],issued['verification_id'],booking.digest(row['phone']),stamp(row),time.time()+900))
    return {**issued,'challenge':challenge,'message':MESSAGE}


def context(db, data, *, lock=False):
    challenge = data.get('challenge')
    owner(challenge)
    sql = '''SELECT r.*,m.salt,m.password_hash,a.phone FROM password_reset_requests r
             JOIN members m ON m.id=r.member_id JOIN mobile_accounts a ON a.member_id=m.id
             JOIN user_accounts u ON u.member_id=m.id WHERE r.challenge_hash=?'''
    if lock and not isinstance(db, sqlite3.Connection):
        sql += ' FOR UPDATE OF m'
    row = db.execute(sql, (booking.digest(challenge),)).fetchone()
    if (not row or row['expires'] <= time.time() or row['verification_id'] != data.get('verification_id')
            or not hmac.compare_digest(row['auth_stamp'],stamp(row))
            or not hmac.compare_digest(row['phone_hash'],booking.digest(row['phone']))
            or mobile_accounts.disabled(db,row['member_id'])):
        raise accounts.AccountError('Reset verification expired. Request another code.',410)
    return row


def verify(path, data):
    with core.connect(path) as db:
        context(db,data)
    return booking_phone.verify_code(path,owner(data['challenge']),data.get('verification_id'),data.get('code'))


def save(path, data):
    password = data.get('password')
    if not isinstance(password,str) or not 12 <= len(password) <= 128:
        raise accounts.AccountError('Use a password of 12–128 characters.')
    if password != data.get('confirm_password'):
        raise accounts.AccountError('Passwords do not match.')
    with booking.connection(path,write=True) as db:
        row = context(db,data,lock=True)
        booking_phone.require_verified(db,booking.digest(owner(data['challenge'])),data['verification_id'],data.get('verification_token'),row['phone'])
        salt = secrets.token_hex(16)
        hashed = 'scrypt-v1$' + accounts.password_hash(password,salt)
        db.execute('UPDATE members SET salt=?,password_hash=? WHERE id=?',(salt,hashed,row['member_id']))
        db.execute('DELETE FROM guest_usage WHERE member_id=?',(row['member_id'],))
        db.execute('DELETE FROM sessions WHERE member_id=?',(row['member_id'],))
        db.execute("UPDATE booking_phone_checks SET state='consumed',proof_hash=NULL WHERE id=?",(data['verification_id'],))
        # Changing the password invalidates every pending reset for this account.
        db.execute('DELETE FROM password_reset_requests WHERE member_id=?',(row['member_id'],))
    return {'ok':True,'message':'Password reset successfully. Log in with your new password.'}
