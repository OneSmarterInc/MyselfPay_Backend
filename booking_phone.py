"""Owner-bound, short-lived mobile verification proofs backed by Twilio Verify."""
import hmac
import re
import secrets
import sqlite3
import time
import booking
import booking_sms


def phone_number(value):
    if not isinstance(value, str):
        raise booking.BookingError('Enter your mobile number with its country code, such as +15551234567.')
    value = re.sub(r'[ ()\-.]', '', value.strip())
    if not re.fullmatch(r'\+[1-9][0-9]{7,14}', value):
        raise booking.BookingError('Enter your mobile number with its country code, such as +15551234567.')
    return value


def request_code(path, owner, phone):
    phone = phone_number(phone)
    try:
        booking_sms.configuration()
    except booking_sms.SMSUnavailable as exc:
        raise booking.BookingError(str(exc), 503) from None
    owner_hash, phone_hash = booking.digest(owner), booking.digest(phone)
    now = time.time()
    with booking.connection(path, write=True) as db:
        allowed = True
        for key, window, limit in [('owner:' + owner_hash, int(now//3600), 5), ('phone:' + phone_hash, int(now//3600), 5), ('global-day', int(now//86400), 500)]:
            row = db.execute('INSERT INTO booking_sms_limits VALUES(?,?,1) ON CONFLICT(key,window_id) DO UPDATE SET attempts=booking_sms_limits.attempts+1 RETURNING attempts', (key,window)).fetchone()
            allowed = allowed and row['attempts'] <= limit
    if not allowed:
        raise booking.BookingError('Mobile code limit reached. Please try again later.', 429)
    ident = secrets.token_urlsafe(24)
    with booking.connection(path, write=True) as db:
        db.execute("INSERT INTO settings(key,value) VALUES(?,'1') ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('booking-phone-owner:' + owner_hash,))
        query='SELECT * FROM booking_phone_checks WHERE owner_hash=? AND phone_hash=?'
        if not isinstance(db, sqlite3.Connection): query+=' FOR UPDATE'
        prior=db.execute(query,(owner_hash,phone_hash)).fetchone()
        if prior and prior['last_sent'] > now-60:
            raise booking.BookingError('Wait 60 seconds before requesting another SMS code.',429)
        if prior and prior['attempts'] >= 5 and prior['created'] > now-3600:
            raise booking.BookingError('Too many incorrect mobile codes. Try again in an hour.',429)
        attempts=prior['attempts'] if prior and prior['created'] > now-3600 else 0
        created=prior['created'] if attempts else now
        encrypted=booking.cipher().encrypt(phone.encode()).decode()
        db.execute("""INSERT INTO booking_phone_checks(id,owner_hash,phone_hash,phone_encrypted,state,attempts,created,last_sent,expires)
            VALUES(?,?,?,?,'sending',?,?,?,?) ON CONFLICT(owner_hash,phone_hash) DO UPDATE SET
            id=excluded.id,phone_encrypted=excluded.phone_encrypted,provider_sid=NULL,proof_hash=NULL,
            state='sending',attempts=excluded.attempts,created=excluded.created,last_sent=excluded.last_sent,expires=excluded.expires""",
            (ident,owner_hash,phone_hash,encrypted,attempts,created,now,now+600))
    try:
        sid=booking_sms.send_code(phone)
    except booking_sms.SMSUnavailable as exc:
        with booking.connection(path,write=True) as db:
            db.execute("UPDATE booking_phone_checks SET state='failed' WHERE id=?",(ident,))
        raise booking.BookingError(str(exc),503) from None
    with booking.connection(path,write=True) as db:
        db.execute("UPDATE booking_phone_checks SET state='sent',provider_sid=? WHERE id=? AND state='sending'",(sid,ident))
    return {'verification_id':ident,'expires_in':600,'resend_after':60,'message':'SMS verification code sent.'}


def verify_code(path, owner, ident, code):
    if not isinstance(ident,str) or not isinstance(code,str) or not re.fullmatch(r'[0-9]{6}',code):
        raise booking.BookingError('Enter the six-digit code from the SMS.')
    now=time.time()
    with booking.connection(path,write=True) as db:
        query='SELECT * FROM booking_phone_checks WHERE id=? AND owner_hash=?'
        if not isinstance(db,sqlite3.Connection): query+=' FOR UPDATE'
        row=db.execute(query,(ident,booking.digest(owner))).fetchone()
        if not row or row['state']!='sent' or row['expires']<=now:
            raise booking.BookingError('Mobile code expired, is being checked, or was already used. Request another code.',410)
        if row['attempts']>=5:
            raise booking.BookingError('Too many incorrect mobile codes. Try again in an hour.',429)
        db.execute("UPDATE booking_phone_checks SET state='checking',attempts=attempts+1 WHERE id=?",(ident,))
        sid=row['provider_sid']
    try:
        approved=booking_sms.check_code(sid,code)
    except booking_sms.SMSUnavailable as exc:
        with booking.connection(path,write=True) as db:
            db.execute("UPDATE booking_phone_checks SET state='sent' WHERE id=? AND state='checking'",(ident,))
        raise booking.BookingError(str(exc),503) from None
    proof=secrets.token_urlsafe(32) if approved else None
    with booking.connection(path,write=True) as db:
        if approved:
            result=db.execute("UPDATE booking_phone_checks SET state='verified',proof_hash=?,expires=? WHERE id=? AND state='checking'",(booking.digest(proof),now+900,ident))
            if result.rowcount != 1:
                raise booking.BookingError('Mobile verification changed. Request another code.',410)
        else:
            db.execute("UPDATE booking_phone_checks SET state='sent' WHERE id=? AND state='checking'",(ident,))
    if not approved:
        raise booking.BookingError('Incorrect or expired SMS code. Check the code and try again.')
    return {'verification_id':ident,'verification_token':proof,'expires_in':900}


def require_verified(db, owner_hash, ident, proof, phone):
    if not isinstance(ident,str) or not isinstance(proof,str):
        raise booking.BookingError('Verify your mobile number before submitting this request.',403)
    query='SELECT * FROM booking_phone_checks WHERE id=? AND owner_hash=?'
    if not isinstance(db,sqlite3.Connection): query+=' FOR UPDATE'
    row=db.execute(query,(ident,owner_hash)).fetchone()
    if not row or row['state']!='verified' or row['expires']<=time.time() or not hmac.compare_digest(booking.digest(proof),row['proof_hash'] or ''):
        raise booking.BookingError('Mobile verification expired or was already used. Verify your number again.',403)
    normalized=phone_number(phone)
    if not hmac.compare_digest(booking.digest(normalized),row['phone_hash']):
        raise booking.BookingError('The mobile number changed. Verify it again.',403)
    return normalized
