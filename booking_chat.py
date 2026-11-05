"""Submission-triggered durable handoff to independently hosted Secure Mail."""
import hashlib
import hmac
import json
import logging
import os
import secrets
import ssl
import time
import uuid
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler
from urllib.error import HTTPError, URLError


logger = logging.getLogger(__name__)


def log_handoff_failure(operation: str, error: Exception) -> None:
    """Report only fixed categories; exception text can contain patient tokens."""
    cause = error.reason if isinstance(error, URLError) else error
    status = error.code if isinstance(error, HTTPError) else 0
    if isinstance(error, HTTPError):
        category = 'http_rejected'
    elif isinstance(cause, ssl.SSLError):
        category = 'tls_failed'
    elif isinstance(cause, TimeoutError):
        category = 'timeout'
    elif isinstance(error, URLError):
        category = 'connection_failed'
    elif isinstance(error, (ValueError, KeyError)):
        category = 'configuration_or_response_invalid'
    else:
        category = 'unexpected_failure'
    logger.warning('secure_mail_handoff operation=%s category=%s http_status=%s',
                   operation, category, status)


def enabled():
    return os.getenv('SECURE_MAIL_ENABLED', 'false').lower() == 'true'


def enqueue(con, row):
    if enabled() and row['status'] in ('requested', 'voicemail', 'scheduled'):
        con.execute("INSERT INTO booking_chat(request_id,created) VALUES(?,?) ON CONFLICT(request_id) DO NOTHING", (row['id'], time.time()))
        if row['status'] == 'scheduled':
            enqueue_confirmation(con, row)


def origin(name):
    value = os.environ[name].rstrip('/')
    parsed = urlsplit(value)
    local = os.getenv('SECURE_MAIL_ALLOW_LOOPBACK_HTTP') == 'true' and parsed.hostname in ('localhost', '127.0.0.1')
    if (parsed.scheme != 'https' and not (local and parsed.scheme == 'http')) or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path:
        raise ValueError('Invalid Secure Mail origin')
    return value


def link(thread_id):
    ident = str(uuid.UUID(thread_id))
    return origin('SECURE_MAIL_PORTAL_URL') + '/recipient/inbox?source=' + quote(os.environ['SECURE_MAIL_KEY_ID'], safe='') + '#thread/' + ident


def summary(con, row):
    if not enabled() or con is None:
        return {}
    chat = con.execute('SELECT state,thread_id FROM booking_chat WHERE request_id=?', (row['id'],)).fetchone()
    if chat and chat['state'] == 'ready':
        return {'secure_chat_url': link(chat['thread_id']), 'secure_chat_status': 'ready'}
    return {'secure_chat_status': 'pending'} if chat else {}


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Secure Mail redirects are not allowed')


def create_thread(ident, payload):
    import booking
    slots = '\n'.join(f"- {slot['date']}: {booking.PERIODS[slot['period']]}" for slot in payload['slots'])
    body = (f"Appointment request: {ident}\nClinic: {payload['clinic_name']}\n"
            f"Patient: {payload['first_name']}\nEmail: {payload['patient_email']}\nPhone: {payload.get('phone', '')}\n"
            f"Service: {payload['service']}\nExam: {payload['exam']}\nContrast: {payload['contrast']}\n"
            f"Requested slots ({payload['timezone']}):\n{slots}\nThe clinic confirms final scheduling with the patient.")
    data = {'external_id': ident, 'subject': 'Appointment request - ' + payload['clinic_name'], 'body': body,
            'provider_action': {'url': payload['clinic_url'], 'expires_at': payload['clinic_link_expires']},
            'participants': [{'email': payload['patient_email'], 'name': payload['first_name'], 'role': 'requester'},
                             {'email': payload['recipient'], 'name': payload['clinic_name'], 'role': 'provider'}]}
    result = signed_request(data)
    if result.get('provider_notification') != 'queued':
        raise ValueError('Clinic notifications are suppressed in Secure Mail')
    return str(uuid.UUID(result['id']))


def signed_request(data):
    raw = json.dumps(data, separators=(',', ':')).encode()
    path = '/api/integrations/threads'
    source, secret = os.environ['SECURE_MAIL_KEY_ID'], os.environ['SECURE_MAIL_SECRET']
    if len(secret) < 32:
        raise ValueError('Invalid integration secret')
    stamp, nonce = str(int(time.time())), secrets.token_hex(16)
    canonical = '\n'.join(['POST', path, source, stamp, nonce, hashlib.sha256(raw).hexdigest()])
    headers = {'Content-Type':'application/json', 'X-Integration-Key':source, 'X-Integration-Time':stamp,
               'X-Integration-Nonce':nonce, 'X-Integration-Signature':hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()}
    if os.getenv('SECURE_MAIL_ALLOW_LOOPBACK_HTTP') == 'true' and urlsplit(origin('SECURE_MAIL_URL')).hostname in ('localhost', '127.0.0.1'):
        headers['X-Real-IP'] = '127.0.0.1'
    context = ssl.create_default_context(cafile=os.getenv('SECURE_MAIL_CA_FILE') or None)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    if os.getenv('SECURE_MAIL_CLIENT_CERT'):
        context.load_cert_chain(os.environ['SECURE_MAIL_CLIENT_CERT'], os.environ['SECURE_MAIL_CLIENT_KEY'])
    opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
    with opener.open(Request(origin('SECURE_MAIL_URL') + path, data=raw, headers=headers), timeout=15) as response:
        result = json.loads(response.read(32769))
    return result


def dispatch(path, ident):
    import booking
    if not enabled():
        return False
    lease = secrets.token_hex(16)
    with booking.connection(path, write=True) as con:
        row = booking.get_row(con, ident, lock=True)
        if row['status'] not in ('requested', 'voicemail', 'scheduled') or (row['status'] != 'scheduled' and row['link_expires'] <= time.time()):
            return False
        claimed = con.execute("UPDATE booking_chat SET state='sending',lease=?,attempts=attempts+1,next_attempt=? WHERE request_id=? AND state<>'ready' AND next_attempt<=? RETURNING request_id", (lease,time.time()+120,ident,time.time())).fetchone()
        if not claimed:
            return False
        payload = booking.unpack(row)
        payload['clinic_link_expires'] = row['link_expires']
    try:
        thread_id = create_thread(ident, payload)
    except Exception as error:
        log_handoff_failure('create_thread', error)
        # Retry with the same external ID: Secure Mail deduplicates both thread and initial emails.
        with booking.connection(path, write=True) as con:
            con.execute("UPDATE booking_chat SET state='retry',next_attempt=? WHERE request_id=? AND lease=?", (time.time()+60,ident,lease))
        return False
    with booking.connection(path, write=True) as con:
        con.execute("UPDATE booking_chat SET state='ready',thread_id=?,lease=NULL WHERE request_id=? AND lease=?", (thread_id,ident,lease))
    return True


def enqueue_confirmation(con, row):
    """Freeze a patient-only confirmation in the same transaction as approval."""
    import booking
    payload = booking.unpack(row)
    base = payload['clinic_url'].split('/#clinic/', 1)[0]
    tracking = base + '/#request/' + quote(row['id'], safe='') + '/' + quote(payload['patient_token'], safe='')
    content = {
        'body': (f"Your appointment request {row['id']} has been marked scheduled by "
                 f"{payload['clinic_name']}.\nService: {payload['service']}\n"
                 f"Exam: {payload['exam']}\nClinic: {payload['location']}\n"
                 f"Clinic phone: {payload.get('phone_clinic', '')}\n"
                 "Use the request link to review your booking. Confirm the exact appointment time with the clinic."),
        'actions': [{'label': 'View my booking', 'url': tracking,
                     'expires_at': (row['closed'] or time.time()) + 30 * 86400}],
    }
    con.execute(
        "INSERT INTO booking_chat_events(request_id,event_id,payload,created) VALUES(?,?,?,?) "
        "ON CONFLICT(request_id,event_id) DO NOTHING",
        (row['id'], 'booking-confirmed-v1', booking.pack(content), time.time()),
    )


def dispatch_confirmation(path, ident):
    import booking
    if not enabled():
        return False
    lease = secrets.token_hex(16)
    with booking.connection(path, write=True) as con:
        row = booking.get_row(con, ident, lock=True)
        if row['status'] != 'scheduled':
            return False
        chat = con.execute('SELECT state FROM booking_chat WHERE request_id=?', (ident,)).fetchone()
        if not chat or chat['state'] != 'ready':
            return False
        claimed = con.execute(
            "UPDATE booking_chat_events SET state='sending',lease=?,attempts=attempts+1,next_attempt=? "
            "WHERE request_id=? AND event_id='booking-confirmed-v1' AND state<>'ready' "
            "AND next_attempt<=? RETURNING payload,event_id",
            (lease, time.time()+120, ident, time.time()),
        ).fetchone()
        if not claimed:
            return False
        content = booking.unpack(claimed)
    try:
        result = signed_request({**content, 'event_type': 'booking_confirmed',
                                 'external_id': ident, 'event_id': claimed['event_id']})
        str(uuid.UUID(result['id']))
    except Exception as error:
        log_handoff_failure('booking_confirmed', error)
        with booking.connection(path, write=True) as con:
            con.execute("UPDATE booking_chat_events SET state='retry',next_attempt=? WHERE request_id=? AND lease=?",
                        (time.time()+60, ident, lease))
        return False
    with booking.connection(path, write=True) as con:
        con.execute("UPDATE booking_chat_events SET state='ready',lease=NULL WHERE request_id=? AND lease=?",
                    (ident, lease))
    return True
