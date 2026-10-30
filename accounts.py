"""Public account registration/login; guest identities remain separate."""
import hashlib
import hmac
import re
import secrets
import time
import uuid
from pathlib import Path
import core

class AccountError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status

def initialize(path):
    with core.connect(path) as db:
        db.executescript((Path(__file__).parent / 'ops/sql/20261030-user-accounts.sql').read_text())

def email_address(value):
    if not isinstance(value, str): raise AccountError('Enter a valid email address.')
    value = value.strip().lower()
    if len(value) > 254 or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}", value):
        raise AccountError('Enter a valid email address.')
    if value.endswith(('.invalid', '.test')):
        raise AccountError('Enter a valid email address.')
    return value

def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32).hex()

def throttle(path, action, ip, email):
    """Counters commit before validation; no raw email/IP is persisted."""
    now = int(time.time())
    checks = [('account:global', 60, 120), (f'account:{action}:ip:{ip}', 60, 10 if action == 'login' else 5),
              (f'account:{action}:email:{email}', 3600, 30 if action == 'login' else 5)]
    allowed = True
    with core.connect(path) as db:
        for key, seconds, limit in checks:
            key = hashlib.sha256(key.encode()).hexdigest()
            # Shared table uses minute window IDs so existing retention still applies.
            window = (now // seconds) * (seconds // 60)
            row = db.execute('INSERT INTO login_limits VALUES(?,?,1) ON CONFLICT(key,window_id) DO UPDATE SET attempts=login_limits.attempts+1 RETURNING attempts', (key, window)).fetchone()
            allowed = row['attempts'] <= limit and allowed
    if not allowed: raise AccountError('Too many attempts. Please try again later.', 429)

def authenticate(path, data, action, ip, previous_token=''):
    raw_email = data.get('email', '')
    normalized = raw_email.strip().lower()[:254] if isinstance(raw_email, str) else ''
    throttle(path, action, ip, normalized)
    email = email_address(raw_email)
    password = data.get('password', '')
    if not isinstance(password, str) or not 1 <= len(password) <= 128:
        raise AccountError('Enter a password of no more than 128 characters.')
    if action == 'register':
        name, phone = data.get('name', ''), data.get('phone', '')
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or any(ord(c) < 32 for c in name):
            raise AccountError('Enter your name (1–100 characters).')
        if not isinstance(phone, str) or (phone and not re.fullmatch(r'[+0-9 ()\-\.]{7,30}', phone)):
            raise AccountError('Enter a valid phone number or leave it blank.')
        if len(password) < 12: raise AccountError('Use a password of 12–128 characters.')
        ident, salt = str(uuid.uuid4()), secrets.token_hex(16)
        hashed = 'scrypt-v1$' + password_hash(password, salt)
        with core.connect(path) as db:
            # Serialize duplicate checks without changing any existing member record.
            db.execute("INSERT INTO settings(key,value) VALUES('account-registration-lock','1') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            if db.execute('SELECT id FROM members WHERE lower(email)=?', (email,)).fetchone():
                raise AccountError('Unable to create an account with these details. Try logging in.', 409)
            db.execute('INSERT INTO members(id,tenant,email,name,salt,password_hash) VALUES(?,?,?,?,?,?)', (ident, 'selfpay', email, name.strip(), salt, hashed))
            db.execute('INSERT INTO user_accounts(member_id,email,phone,created) VALUES(?,?,?,?)', (ident, email, phone.strip(), time.time()))
            return new_session(db, ident, previous_token)
    with core.connect(path) as db:
        row = db.execute('SELECT m.id,m.salt,m.password_hash FROM members m JOIN user_accounts a ON a.member_id=m.id WHERE a.email=?', (email,)).fetchone()
        salt = row['salt'] if row else '00' * 16
        candidate = 'scrypt-v1$' + password_hash(password, salt)
        if not hmac.compare_digest(candidate, row['password_hash'] if row else 'scrypt-v1$' + '0' * 64):
            raise AccountError('Email or password is incorrect.', 401)
        return new_session(db, row['id'], previous_token)

def new_session(db, ident, previous_token):
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    if previous_token:
        old = hashlib.sha256(previous_token.encode()).hexdigest()
        db.execute('DELETE FROM guest_usage WHERE session_hash=?', (old,))
        db.execute('DELETE FROM sessions WHERE token_hash=?', (old,))
    db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (hashlib.sha256(token.encode()).hexdigest(), ident, csrf, time.time() + 8*3600))
    member = dict(db.execute('SELECT id,tenant,name,email FROM members WHERE id=?', (ident,)).fetchone())
    return {'token': token, 'csrf': csrf, 'member': member}

