"""Email-verified appointment requests with encrypted details.

Patient and clinic verification use SMTP. Clinic notifications use an explicit
delivery switch and committed outbox. Recipients come from the catalog only.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.headerregistry import Address
from html import escape
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any, Iterator
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
import core
import booking_mail
import booking_delivery

ZONES = {
    'America/New_York': 'Eastern', 'America/Chicago': 'Central',
    'America/Denver': 'Mountain', 'America/Phoenix': 'Arizona',
    'America/Los_Angeles': 'Pacific', 'America/Anchorage': 'Alaska',
    'Pacific/Honolulu': 'Hawaii',
}
PERIODS = {'morning': 'Morning (8 am–12 pm)', 'afternoon': 'Afternoon (12–5 pm)'}
TERMINAL = {'scheduled', 'unable', 'expired'}
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+\Z")


class BookingError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def cipher() -> Fernet:
    key = os.environ.get('BOOKING_ENCRYPTION_KEY')
    if not key:
        raise BookingError('Appointment requests are not configured.', 503)
    return Fernet(key.encode())


@contextmanager
def connection(path: Any, *, write: bool = False) -> Iterator[Any]:
    con = core.connect(path)
    try:
        with con:
            if write and isinstance(con, sqlite3.Connection):
                con.execute('BEGIN IMMEDIATE')
            yield con
    finally:
        if isinstance(con, sqlite3.Connection):
            con.close()


def initialize_local(path: Any) -> None:
    """Only the loopback server calls this. Hosted schema changes are manual."""
    if str(path).startswith(('postgres://', 'postgresql://')):
        return
    key_path = Path(path).parent / 'booking.key'
    if not os.environ.get('BOOKING_ENCRYPTION_KEY'):
        try:
            with key_path.open('xb') as stream:
                stream.write(Fernet.generate_key())
            os.chmod(key_path, 0o600)
        except FileExistsError:
            pass
        os.environ['BOOKING_ENCRYPTION_KEY'] = key_path.read_text().strip()
    cipher()
    with connection(path) as con:
        con.executescript((core.ROOT / 'ops/booking-schema.sql').read_text())
        con.executescript((core.ROOT / 'ops/booking-outbox.sql').read_text())


def appointment_email(center: dict) -> str:
    scope = str(center.get('email_scope', ''))
    excluded = r'not (?:intended for |for )?(?:patient |branch |local )?schedul|not intended for prescriptions|billing|financial|business|referral|prescriptions|feedback|advocacy|privacy|media'
    if center.get('appointment_email_approved') is not True and (
        not re.search(r'scheduling|appointments?', scope, re.I)
        or re.search(excluded, scope, re.I)
    ):
        return ''
    address = str(center.get('email', '')).strip()
    return address if len(address) <= 254 and EMAIL.fullmatch(address) else ''


def resolve_center(con: Any, key: str) -> dict:
    row = con.execute(
        'SELECT payload FROM entries WHERE batch_id=? AND entry_key=?',
        (core.active_batch(con), key),
    ).fetchone()
    if not row:
        raise BookingError('This listing is no longer available. Refresh the directory.', 409)
    return json.loads(row['payload'])


def price_snapshot(con: Any, key: str, offer_id: str, service: str = '') -> str:
    if not offer_id:
        return 'Clinic will confirm the exact total.'
    row = con.execute('SELECT payload,amount_cents,max_cents FROM offers WHERE batch_id=? AND entry_key=? AND id=?',
                      (core.active_batch(con), key, offer_id)).fetchone()
    if not row:
        raise BookingError('This price offer changed. Refresh the listing.', 409)
    offer = json.loads(row['payload'])
    if service and offer.get('mod') != service:
        return 'Clinic will confirm the exact total for the selected service.'
    amount = row['amount_cents']
    label = f"${amount / 100:,.2f}" if amount is not None else 'Price on request'
    kind = offer.get('type', 'quote')
    if kind in ('quote', 'conflict'):
        label = 'Price requires confirmation'
    elif kind == 'range' and row['max_cents'] is not None:
        label += f"–${row['max_cents'] / 100:,.2f}"
    elif kind == 'starting':
        label = 'From ' + label
    elif kind == 'maximum':
        label = 'Up to ' + label
    elif kind == 'average':
        label = 'Published average ' + label
    return f"{label} · {offer.get('type', 'quote')} · {offer.get('included', 'Confirm inclusions with clinic')}. Final price requires clinic confirmation."


def options(path: Any, key: str, offer_id: str = '') -> dict:
    with connection(path) as con:
        center = resolve_center(con, key)
        price_text = price_snapshot(con, key, offer_id)
    return {
        'name': center['name'], 'location': center.get('area', ''),
        'price_text': price_text,
        'delivery': booking_delivery.mode(appointment_email(center)), 'recipient_available': bool(appointment_email(center)),
        'is_test_center': bool(center.get('is_test_center')),
        'zones': ZONES, 'periods': PERIODS,
        'today': {z: datetime.now(ZoneInfo(z)).date().isoformat() for z in ZONES},
    }


def text_field(data: dict, field: str, limit: int) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise BookingError(f'Enter a valid {field.replace("_", " ")}.')
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise BookingError(f'Enter a valid {field.replace("_", " ")}.')
    return value.strip()


def validate(data: dict) -> dict:
    name = text_field(data, 'first_name', 80)
    phone = text_field(data, 'phone', 30)
    if not re.fullmatch(r'\+?[0-9 ()\-.]+', phone) or not 10 <= len(re.sub(r'\D', '', phone)) <= 15:
        raise BookingError('Enter a valid phone number, including area code.')
    zone = data.get('timezone')
    if zone not in ZONES:
        raise BookingError('Choose the clinic’s time zone.')
    service = data.get('service')
    if service not in ('MRI', 'CT', 'Other imaging', 'Please contact me'):
        raise BookingError('Choose an imaging service.')
    exam = str(data.get('exam', '')).strip()
    if len(exam) > 100 or any(ord(c) < 32 for c in exam):
        raise BookingError('Keep the exam description under 100 characters.')
    contrast = data.get('contrast', 'unknown')
    if contrast not in ('unknown', 'without', 'with', 'both'):
        raise BookingError('Choose a valid contrast preference.')
    if data.get('consent') is not True:
        raise BookingError('Please consent to the clinic contacting you about this request.')
    slots = data.get('slots')
    if not isinstance(slots, list) or not 1 <= len(slots) <= 3:
        raise BookingError('Choose between one and three preferred times.')
    normalized = []
    now = datetime.now(ZoneInfo(zone))
    for slot in slots:
        if not isinstance(slot, dict) or slot.get('period') not in PERIODS:
            raise BookingError('Choose a valid time window.')
        try:
            raw = slot['date']
            day = datetime.strptime(raw, '%Y-%m-%d').date()
            if day.isoformat() != raw:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            raise BookingError('Choose a valid date.') from None
        start = datetime(day.year, day.month, day.day,
                         8 if slot['period'] == 'morning' else 12, tzinfo=ZoneInfo(zone))
        if start <= now or day > now.date() + timedelta(days=90):
            raise BookingError('Choose future times within the next 90 days.')
        item = {'date': raw, 'period': slot['period']}
        if item in normalized:
            raise BookingError('Each preferred time must be different.')
        normalized.append(item)
    return {'first_name': name, 'phone': phone, 'timezone': zone, 'service': service,
            'exam': exam, 'contrast': contrast, 'slots': sorted(normalized, key=lambda s: (s['date'], 0 if s['period']=='morning' else 1)),
            'consent': True, 'consent_version': 'booking-v1'}


def unpack(row: Any) -> dict:
    return json.loads(cipher().decrypt(row['payload'].encode()))


def pack(payload: dict) -> str:
    return cipher().encrypt(core.canonical(payload).encode()).decode()


def notification(payload: dict, ident: str) -> EmailMessage:
    """Clinic request email: patient contact details stay behind OTP verification."""
    message = EmailMessage()
    message['From'] = Address('MySelfPay', addr_spec=os.environ.get('SMTP_FROM', 'info@kormic.ai'))
    if payload['recipient']:
        message['To'] = payload['recipient']
    message['Subject'] = f'New self-pay request {ident}'
    message['Message-ID'] = f'<selfpay-{ident}@{os.environ.get("SMTP_FROM", "no-reply@example.invalid").rsplit("@", 1)[-1]}>'
    slots = '; '.join(f"{s['date']} {PERIODS[s['period']]}" for s in payload['slots'])
    exam = ' '.join(filter(None, (payload['service'], payload['exam'])))
    contrast = {'unknown': 'Not sure — confirm with patient', 'without': 'Without contrast', 'with': 'With contrast', 'both': 'Without and with contrast'}[payload['contrast']]
    message.set_content(
        f"New self-pay request {ident}\nClinic: {payload['clinic_name']}\n"
        f"Exam: {exam}\nContrast: {contrast}\nPreferred times: {slots} ({ZONES[payload['timezone']]} time)\n"
        f"Price: {payload.get('price_text', 'Confirm directly with the patient.')}\n"
        + ('TEST CENTER: This is a synthetic booking workflow; no medical care is provided.\n' if payload.get('is_test_center') else '') +
        'Open the secure link to review patient contact details.\n'
        f"{payload['clinic_url']}\n\n"
        'This link expires in 72 hours. A one-time code is required.\n'
        'This is a request, not a confirmed appointment.\n\n'
        'Please do not reply to this automated email.'
    )
    message.add_alternative(f'''<html lang="en"><body style="background:#f4f5f6;font-family:Arial,sans-serif;color:#22272b;padding:24px">
<div style="max-width:560px;margin:auto;background:white;padding:32px;border-radius:14px">
<p style="font-size:26px;font-weight:bold">MySelf<span style="color:#b51d2b">Pay</span></p>
<h1 style="font-size:24px">New self-pay request</h1><p>{escape(ident)}</p>
<p><strong>Clinic:</strong> {escape(payload['clinic_name'])}</p><p><strong>Exam:</strong> {escape(exam)}</p>
<p><strong>Contrast:</strong> {escape(contrast)}</p>
<p><strong>Preferred times:</strong> {escape(slots)} ({escape(ZONES[payload['timezone']])} time)</p>
<p><strong>Price:</strong> {escape(payload.get('price_text', 'Confirm directly with the patient.'))}</p>
{'<p><strong>TEST CENTER — synthetic booking workflow. No medical care is provided.</strong></p>' if payload.get('is_test_center') else ''}
<p><a href="{escape(payload['clinic_url'], quote=True)}" style="display:inline-block;padding:14px 22px;background:#b51d2b;color:white;text-decoration:none;border-radius:8px">Open request</a></p>
<p>The patient's contact details open after verification. This link expires in 72 hours.</p>
<p>This is a request, not a confirmed appointment.</p><hr style="border:0;border-top:1px solid #eceef0">
<p style="font-size:12px;color:#68727a">MySelfPay by One Smarter, Inc.<br>Please do not reply to this automated email.</p>
</div></body></html>''', subtype='html')
    return message


def suppress(message: EmailMessage) -> None:
    """Deliberate null transport: never connect, print, spool or enqueue mail."""
    # Validate construction without retaining patient data or claiming delivery.
    message.as_bytes()


def email_address(value: Any) -> str:
    if not isinstance(value, str):
        raise BookingError('Enter a valid personal email address.')
    value = value.strip()
    if len(value) > 254 or not EMAIL.fullmatch(value):
        raise BookingError('Enter a valid personal email address.')
    local, domain = value.rsplit('@', 1)
    return local + '@' + domain.lower()


def code_digest(ident: str, code: str) -> str:
    cipher()  # Fail closed if encryption is unconfigured.
    return hmac.new(os.environ['BOOKING_ENCRYPTION_KEY'].encode(),
                    (ident + ':' + code).encode(), hashlib.sha256).hexdigest()


def request_patient_code(path: Any, owner: str, email: Any) -> dict:
    email = email_address(email)
    owner_hash, email_hash = digest(owner), digest(email.casefold())
    now = time.time()
    # Commit counters even on throttling; counters contain no raw addresses.
    with connection(path, write=True) as con:
        checks = [('owner:' + owner_hash, int(now // 3600), 5),
                  ('email:' + email_hash, int(now // 3600), 5),
                  ('global-day', int(now // 86400), 500)]
        allowed = True
        for key, window, limit in checks:
            row = con.execute('''INSERT INTO booking_mail_limits VALUES(?,?,1)
                ON CONFLICT(key,window_id) DO UPDATE SET attempts=booking_mail_limits.attempts+1
                RETURNING attempts''', (key, window)).fetchone()
            allowed = allowed and row['attempts'] <= limit
    if not allowed:
        raise BookingError('Email code limit reached. Please try again later.', 429)
    code = f'{secrets.randbelow(1000000):06d}'
    ident = secrets.token_urlsafe(24)
    with connection(path, write=True) as con:
        query = 'SELECT * FROM booking_email_checks WHERE owner_hash=? AND email_hash=?'
        if not isinstance(con, sqlite3.Connection):
            query += ' FOR UPDATE'
        prior = con.execute(query, (owner_hash, email_hash)).fetchone()
        if prior and prior['last_sent'] > now - 60:
            raise BookingError('Wait 60 seconds before requesting another code.', 429)
        if prior and prior['attempts'] >= 5 and prior['created'] > now - 3600:
            raise BookingError('Too many incorrect codes. Try again in an hour.', 429)
        encrypted = cipher().encrypt(email.encode()).decode()
        attempts = prior['attempts'] if prior and prior['created'] > now - 3600 else 0
        created = prior['created'] if attempts else now
        # A rotated challenge invalidates previous codes and verification proofs.
        con.execute('''INSERT INTO booking_email_checks
            (id,owner_hash,email_hash,email_encrypted,code_hash,state,attempts,created,last_sent,expires)
            VALUES(?,?,?,?,?,'sending',?,?,?,?)
            ON CONFLICT(owner_hash,email_hash) DO UPDATE SET
            id=excluded.id,email_encrypted=excluded.email_encrypted,code_hash=excluded.code_hash,
            proof_hash=NULL,state='sending',attempts=excluded.attempts,created=excluded.created,
            last_sent=excluded.last_sent,expires=excluded.expires''',
            (ident, owner_hash, email_hash, encrypted, code_digest(ident, code), attempts, created, now, now + 600))
    try:
        booking_mail.send_patient_code(email, code)
    except booking_mail.MailUnavailable as exc:
        with connection(path, write=True) as con:
            con.execute("UPDATE booking_email_checks SET state='failed',code_hash=NULL WHERE id=?", (ident,))
        raise BookingError(str(exc), 503) from None
    with connection(path, write=True) as con:
        con.execute("UPDATE booking_email_checks SET state='sent' WHERE id=? AND state='sending'", (ident,))
    return {'verification_id': ident, 'expires_in': 600, 'resend_after': 60,
            'message': 'Verification email submitted. Check your inbox and spam folder.'}


def verify_patient_code(path: Any, owner: str, ident: Any, code: Any) -> dict:
    if not isinstance(ident, str) or not isinstance(code, str) or not re.fullmatch(r'\d{6}', code):
        raise BookingError('Enter the six-digit code from your email.')
    now, failure = time.time(), None
    with connection(path, write=True) as con:
        query = 'SELECT * FROM booking_email_checks WHERE id=? AND owner_hash=?'
        if not isinstance(con, sqlite3.Connection):
            query += ' FOR UPDATE'
        row = con.execute(query, (ident, digest(owner))).fetchone()
        if not row or row['state'] != 'sent' or row['expires'] <= now:
            raise BookingError('Code expired or already used. Request another code.', 410)
        if row['attempts'] >= 5:
            raise BookingError('Too many incorrect codes. Try again in an hour.', 429)
        if not hmac.compare_digest(code_digest(ident, code), row['code_hash'] or ''):
            con.execute('UPDATE booking_email_checks SET attempts=attempts+1 WHERE id=?', (ident,))
            failure = BookingError('Incorrect code. Check your email and try again.')
        else:
            proof = secrets.token_urlsafe(32)
            con.execute("UPDATE booking_email_checks SET state='verified',proof_hash=?,code_hash=NULL,expires=? WHERE id=?",
                        (digest(proof), now + 900, ident))
    if failure:
        raise failure
    return {'verification_id': ident, 'verification_token': proof, 'expires_in': 900}


def verified_patient_email(con: Any, owner_hash: str, ident: Any, proof: Any) -> str:
    if not isinstance(ident, str) or not isinstance(proof, str):
        raise BookingError('Verify your personal email before submitting this request.', 403)
    query = 'SELECT * FROM booking_email_checks WHERE id=? AND owner_hash=?'
    if not isinstance(con, sqlite3.Connection):
        query += ' FOR UPDATE'
    row = con.execute(query, (ident, owner_hash)).fetchone()
    if (not row or row['state'] != 'verified' or row['expires'] <= time.time()
            or not hmac.compare_digest(digest(proof), row['proof_hash'] or '')):
        raise BookingError('Email verification expired or was already used. Verify your email again.', 403)
    return cipher().decrypt(row['email_encrypted'].encode()).decode()


def create(path: Any, owner: str, data: dict, base_url: str) -> dict:
    validated = validate(data)
    key = text_field(data, 'idempotency_key', 80)
    center_key = text_field(data, 'clinic_id', 200)
    owner_hash, key_hash = digest(owner), digest(key)
    offer_id = data.get('offer_id', '')
    if not isinstance(offer_id, str) or len(offer_id) > 200:
        raise BookingError('Invalid price offer.')
    verification_id = data.get('verification_id')
    input_hash = digest(core.canonical({'clinic': center_key, 'offer_id': offer_id,
                                      'verification_id': verification_id, **validated}))
    now = time.time()
    with connection(path, write=True) as con:
        if not isinstance(con, sqlite3.Connection) and isinstance(verification_id, str):
            con.execute('SELECT id FROM booking_email_checks WHERE id=? AND owner_hash=? FOR UPDATE',
                        (verification_id, owner_hash)).fetchone()
        # Unique owner/key and the transaction make retries safe on SQLite and Postgres.
        prior = con.execute('SELECT * FROM booking_requests WHERE owner_hash=? AND idempotency_hash=?',
                            (owner_hash, key_hash)).fetchone()
        if prior:
            if prior['input_hash'] != input_hash:
                raise BookingError('This submission key was already used for different details.', 409)
            return receipt(prior, con)
        patient_email = verified_patient_email(con, owner_hash, verification_id, data.get('verification_token'))
        center = resolve_center(con, center_key)
        if booking_delivery.mode(appointment_email(center)) == 'unavailable':
            raise BookingError('Online booking email is not enabled for this center. Please contact the facility directly.', 503)
        price_text = price_snapshot(con, center_key, offer_id, validated['service'])
        if con.execute('SELECT COUNT(*) AS n FROM booking_requests WHERE owner_hash=? AND created>?',
                       (owner_hash, now - 3600)).fetchone()['n'] >= 5:
            raise BookingError('Request limit reached. Please try again in an hour.', 429)
        ident = 'MSP-' + secrets.token_hex(8).upper()
        patient_token, clinic_token = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        payload = {**validated, 'clinic_id': center_key, 'clinic_name': center['name'],
                   'patient_email': patient_email,
                   'price_text': price_text,
                   'location': center.get('area', ''), 'recipient': appointment_email(center),
                   'phone_clinic': center.get('phone', ''),
                   'is_test_center': bool(center.get('is_test_center')),
                   'patient_token': patient_token, 'clinic_token': clinic_token,
                   'clinic_url': f'{base_url}/#clinic/{ident}/{clinic_token}'}
        # Only server catalog values may become clinic routing destinations.
        if not booking_delivery.enabled():
            suppress(notification(payload, ident))
        con.execute('''INSERT INTO booking_requests
            (id,owner_hash,idempotency_hash,input_hash,payload,patient_hash,clinic_hash,
             status,delivery,created,link_expires) VALUES(?,?,?,?,?,?,?,'requested','suppressed',?,?)
            ON CONFLICT(owner_hash,idempotency_hash) DO NOTHING''',
            (ident, owner_hash, key_hash, input_hash, pack(payload), digest(patient_token),
             digest(clinic_token), now, now + 72 * 3600))
        row = con.execute('SELECT * FROM booking_requests WHERE owner_hash=? AND idempotency_hash=?',
                          (owner_hash, key_hash)).fetchone()
        if row['input_hash'] != input_hash:
            raise BookingError('This submission key was already used for different details.', 409)
        con.execute("UPDATE booking_email_checks SET state='consumed',proof_hash=NULL WHERE id=?", (verification_id,))
        if booking_delivery.enabled():
            booking_delivery.enqueue(con, row['id'])
    # SMTP runs after the booking and outbox commit, never inside the write lock.
    if booking_delivery.enabled():
        booking_delivery.deliver(path, row['id'])
    with connection(path) as con:
        return receipt(get_row(con, row['id']), con)


def receipt(row: Any, con: Any = None) -> dict:
    p = unpack(row)
    delivery = booking_delivery.status(con, row['id'], row['delivery']) if con is not None else row['delivery']
    return {'id': row['id'], 'status': row['status'], 'delivery': delivery,
            'is_test_center': bool(p.get('is_test_center')),
            'clinic_name': p['clinic_name'], 'location': p['location'],
            'price_text': p['price_text'],
            'service': p['service'], 'exam': p['exam'], 'contrast': p['contrast'],
            'slots': p['slots'], 'timezone': p['timezone'],
            'created': row['created'], 'closed': row['closed'],
            'tracking_token': p['patient_token']}


def get_row(con: Any, ident: str, *, lock: bool = False) -> Any:
    sql = 'SELECT * FROM booking_requests WHERE id=?'
    if lock and not isinstance(con, sqlite3.Connection):
        sql += ' FOR UPDATE'
    row = con.execute(sql, (ident,)).fetchone()
    if not row or (row['closed'] or row['link_expires']) + 30 * 86400 <= time.time():
        raise BookingError('This request is unavailable or has expired.', 404)
    return row


def authorize(row: Any, value: Any, field: str) -> None:
    if not isinstance(value, str) or not value or not hmac.compare_digest(digest(value), row[field] or ''):
        raise BookingError('This request is unavailable or access has expired.', 404)


def patient(path: Any, ident: str, token: str, *, preview: bool = False) -> dict:
    with connection(path) as con:
        row = get_row(con, ident)
        authorize(row, token, 'patient_hash')
        result = receipt(row, con)
        if row['status'] not in TERMINAL and row['link_expires'] <= time.time():
            result['status'] = 'expired'
        if preview:
            p = unpack(row)
            result['clinic_url'] = p['clinic_url']
            result['notification'] = notification(p, ident).as_string()
        return result


def clinic_link(row: Any, token: str) -> None:
    authorize(row, token, 'clinic_hash')
    if row['link_expires'] <= time.time():
        raise BookingError('This clinic link has expired.', 410)


def issue_code(path: Any, ident: str, token: str, *, preview: bool = False) -> dict:
    now = time.time()
    with connection(path, write=True) as con:
        row = get_row(con, ident, lock=True)
        clinic_link(row, token)
        recipient = unpack(row)['recipient']
        if not preview and booking_delivery.mode(recipient) != 'enabled':
            raise BookingError('Clinic verification email delivery is not enabled for this request.', 503)
        if row['otp_sent'] and row['otp_sent'] > now - 60:
            raise BookingError('Wait 60 seconds before requesting another code.', 429)
        # A new code does not reset the failed-attempt budget for this link.
        if row['otp_attempts'] >= 5:
            raise BookingError('Too many incorrect codes. Contact the service operator.', 429)
        code = f'{secrets.randbelow(1000000):06d}'
        con.execute('UPDATE booking_requests SET otp_hash=?,otp_expires=?,otp_sent=? WHERE id=?',
                    (digest(token + ':' + code), now + 600, now, ident))
        result = {'delivery': 'suppressed', 'expires_in': 600, 'resend_after': 60}
        if preview:
            result['preview_code'] = code
    if preview:
        return result
    try:
        booking_mail.send_clinic_code(recipient, ident, code)
    except booking_mail.MailUnavailable as exc:
        with connection(path, write=True) as con:
            con.execute('UPDATE booking_requests SET otp_hash=NULL,otp_expires=NULL WHERE id=? AND otp_sent=?', (ident, now))
        raise BookingError(str(exc), 503) from None
    return {'delivery': 'sent', 'expires_in': 600, 'resend_after': 60}


def verify_code(path: Any, ident: str, token: str, code: str) -> dict:
    now = time.time()
    failure = None
    with connection(path, write=True) as con:
        row = get_row(con, ident, lock=True)
        clinic_link(row, token)
        if row['otp_attempts'] >= 5:
            raise BookingError('Too many incorrect codes.', 429)
        if not row['otp_hash'] or not row['otp_expires'] or row['otp_expires'] <= now:
            raise BookingError('Code expired or already used. Request another code.', 410)
        if not isinstance(code, str) or not hmac.compare_digest(digest(token + ':' + code), row['otp_hash']):
            con.execute('UPDATE booking_requests SET otp_attempts=otp_attempts+1 WHERE id=?', (ident,))
            failure = BookingError('Incorrect code. Please try again.', 400)
        else:
            session = secrets.token_urlsafe(32)
            con.execute('''UPDATE booking_requests SET otp_hash=NULL,otp_expires=NULL,
                clinic_session_hash=?,clinic_session_expires=? WHERE id=?''',
                (digest(session), now + 900, ident))
    if failure:
        raise failure
    return {'session': session, 'expires_at': now + 900}


def clinic_details(path: Any, ident: str, session: str, status: str | None = None) -> dict:
    with connection(path, write=status is not None) as con:
        row = get_row(con, ident, lock=status is not None)
        authorize(row, session, 'clinic_session_hash')
        if not row['clinic_session_expires'] or row['clinic_session_expires'] <= time.time():
            raise BookingError('Your session has expired. Verify a new code.', 401)
        if row['link_expires'] <= time.time():
            raise BookingError('This request link has expired.', 410)
        if status is not None:
            if status not in ('scheduled', 'voicemail', 'unable'):
                raise BookingError('Choose a valid outcome.')
            if row['status'] in TERMINAL and row['status'] != status:
                raise BookingError('This request is already closed.', 409)
            closed = row['closed'] or (time.time() if status in TERMINAL else None)
            con.execute('UPDATE booking_requests SET status=?,closed=? WHERE id=?', (status, closed, ident))
            row = get_row(con, ident)
        p = unpack(row)
        result = receipt(row, con)
        result.pop('tracking_token')
        result.update(first_name=p['first_name'], phone=p['phone'], patient_email=p['patient_email'], expires_at=row['clinic_session_expires'])
        return result


def cleanup(path: Any) -> int:
    now = time.time()
    with connection(path, write=True) as con:
        con.execute("UPDATE booking_requests SET status='expired',closed=link_expires WHERE closed IS NULL AND link_expires<=?", (now,))
        result = con.execute('DELETE FROM booking_requests WHERE closed<=?', (now - 30 * 86400,))
        con.execute('DELETE FROM booking_email_checks WHERE expires<?', (now - 86400,))
        con.execute("DELETE FROM booking_mail_limits WHERE key<>'global-day' AND window_id<?", (int(now // 3600) - 48,))
        con.execute("DELETE FROM booking_mail_limits WHERE key='global-day' AND window_id<?", (int(now // 86400) - 2,))
        return result.rowcount


def lock_session(path: Any, ident: str, session: str) -> dict:
    with connection(path, write=True) as con:
        row = get_row(con, ident, lock=True)
        authorize(row, session, 'clinic_session_hash')
        con.execute('UPDATE booking_requests SET clinic_session_hash=NULL,clinic_session_expires=NULL WHERE id=?', (ident,))
    return {'locked': True}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='Purge appointment details after retention expires.')
    parser.add_argument('--cleanup', action='store_true', required=True)
    parser.parse_args()
    print(f'Deleted {cleanup(core.DB)} expired booking requests.')

