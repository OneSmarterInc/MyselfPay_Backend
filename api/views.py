"""Django HTTP boundary; framework-independent SelfPay services retain their schema."""
import hashlib
import json
import logging
import secrets
import time
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.views.decorators.csrf import csrf_exempt
from django.core.exceptions import RequestDataTooBig
import accounts
import booking
import core

log = logging.getLogger(__name__)

def database():
    return settings.DOMAIN_DATABASE

def reply(data, status=200, token=None):
    response = JsonResponse(data, status=status)
    if token is not None:
        if token:
            response.set_cookie(settings.SESSION_COOKIE_NAME, token, max_age=28800,
                                secure=not settings.DEBUG, httponly=True,
                                samesite=settings.SELFPAY_COOKIE_SAMESITE, path='/')
        else:
            response.delete_cookie(settings.SESSION_COOKIE_NAME, path='/', samesite=settings.SELFPAY_COOKIE_SAMESITE)
    return response

def payload(request):
    if request.content_type != 'application/json':
        raise ValueError('JSON required.')
    if not 0 < len(request.body) <= 100000:
        raise ValueError('Request must contain 1–100000 bytes.')
    data = json.loads(request.body)
    if not isinstance(data, dict):
        raise ValueError('JSON object required.')
    return data

def allowed(key, limit):
    key = hashlib.sha256(key.casefold().encode()).hexdigest()
    with core.connect(database()) as con:
        row = con.execute('INSERT INTO login_limits VALUES(?,?,1) ON CONFLICT(key,window_id) DO UPDATE SET attempts=login_limits.attempts+1 RETURNING attempts', (key, int(time.time() // 60))).fetchone()
    return row['attempts'] <= limit

def public_member(member):
    return {key: value for key, value in member.items() if key != 'csrf'}

@require_GET
def health(request):
    try:
        with core.connect(database()) as con:
            con.execute('SELECT member_id FROM user_accounts LIMIT 0')
            con.execute('SELECT id FROM booking_requests LIMIT 0')
        return reply({'status': 'ok'})
    except Exception:
        return reply({'status': 'unavailable'}, 503)

@csrf_exempt  # Existing API uses a per-session X-CSRF-Token, checked below plus strict Origin middleware.
def dispatch(request, route):
    try:
        return handle(request, route.rstrip('/'))
    except RequestDataTooBig:
        return reply({'error': 'Request too large.'}, 413)
    except (accounts.AccountError, booking.BookingError) as exc:
        return reply({'error': str(exc)}, exc.status)
    except core.GuestAgentLimited as exc:
        response = reply({'error': str(exc), 'code': 'guest_preview_limit', 'reason': exc.reason, 'retry_after': exc.retry_after}, 429)
        if exc.retry_after:
            response['Retry-After'] = str(exc.retry_after)
        return response
    except core.GuestCreationLimited as exc:
        response = reply({'error': str(exc), 'retry_after': exc.retry_after}, 429)
        response['Retry-After'] = str(exc.retry_after)
        return response
    except PermissionError:
        return reply({'error': 'Record unavailable.'}, 404)
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, KeyError, AttributeError):
        return reply({'error': 'Invalid request.'}, 400)
    except ValueError as exc:
        return reply({'error': str(exc)}, 400)
    except Exception:
        # Never log request bodies, tokens, database URLs or exception values.
        log.error('SelfPay service request failed')
        return reply({'error': 'Service temporarily unavailable. Please retry.'}, 503)

def handle(request, route):
    method = request.method
    if method not in ('GET', 'POST', 'DELETE'):
        return reply({'error': 'Method not allowed.'}, 405)
    db = database()
    token = request.COOKIES.get(settings.SESSION_COOKIE_NAME, '')
    member = core.session(token, db)
    ip = request.META.get('REMOTE_ADDR', 'unknown')
    if route == 'directory-summary' and method == 'GET':
        return reply(core.directory_summary(db))
    if method != 'GET' and member and not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), member['csrf']):
        return reply({'error': 'CSRF token required.'}, 403)
    if route in ('register', 'login'):
        if method != 'POST':
            return reply({'error': 'Use POST.'}, 405)
        data = accounts.authenticate(db, payload(request), route, ip, token)
        return reply(data, 201 if route == 'register' else 200, data.pop('token'))
    if route == 'password':
        return reply({'error': 'Password recovery is not available yet.'}, 410)
    if route == 'guest' and method == 'POST':
        payload(request)
        if not allowed('guest:' + ip, 20):
            return reply({'error': 'Too many requests. Wait one minute.'}, 429)
        if member and member['tenant'] == 'guest':
            return reply({'member': public_member(member), 'csrf': member['csrf']})
        data = core.guest_login(db)
        return reply(data, token=data.pop('token'))
    if route.startswith('booking/'):
        return booking_request(request, route[8:], member, ip)
    if not member:
        return reply({'error': 'Sign in to your account.'}, 401)
    if route == 'me' and method == 'GET':
        return reply({'member': public_member(member), 'csrf': member['csrf']})
    if route == 'logout' and method == 'POST':
        with core.connect(db) as con:
            key = hashlib.sha256(token.encode()).hexdigest()
            con.execute('DELETE FROM guest_usage WHERE session_hash=?', (key,))
            con.execute('DELETE FROM sessions WHERE token_hash=?', (key,))
        return reply({'ok': True}, token='')
    if route == 'catalog' and method == 'GET':
        return reply(core.catalog(db))
    if route == 'search' and method == 'POST':
        return reply(core.search(payload(request), db))
    if route == 'guest-usage' and method == 'GET':
        return reply({'usage': core.guest_usage(token, db)})
    if route == 'agent' and method == 'POST':
        data = payload(request)
        if member['tenant'] == 'guest':
            return reply(core.guest_agent(token, ip, data.get('message'), data.get('search', {}), data.get('conversation_id'), (3, 5, 500, 30, 2000), db))
        return reply(core.agent(data.get('message'), data.get('search', {}), db))
    if member['tenant'] == 'guest' and (route == 'corrections' or (route.startswith('conversations') and method != 'GET')):
        return reply({'error': 'Sign in to use saved account features.'}, 403)
    if route == 'conversations':
        if method == 'GET':
            return reply({'conversations': core.history(member, db)})
        if method == 'POST':
            return reply(core.save_conversation(member, payload(request), db))
    if route.startswith('conversations/') and method == 'DELETE':
        core.delete_conversation(member, route.split('/')[-1], db)
        return reply({'ok': True})
    if route == 'preferences':
        if method == 'GET':
            return reply(core.preference(member, path=db))
        if method == 'DELETE':
            return reply(core.preference(member, delete=True, path=db))
        data = payload(request)
        if data.get('private'):
            raise ValueError('Private preferences cannot be saved.')
        return reply(core.preference(member, data, path=db))
    if route == 'corrections' and method == 'POST':
        data = payload(request)
        if data.get('private'):
            raise ValueError('Exit private mode before saving a correction report.')
        catalog = core.catalog(db)
        offer = next((o for o in catalog['offers'] if o['id'] == data.get('offer_id')), None)
        if not offer:
            raise ValueError('Offer unavailable.')
        description = str(data.get('description', '')).strip()
        if not 1 <= len(description) <= 1000:
            raise ValueError('Describe the issue in 1–1000 characters.')
        ident = secrets.token_hex(12)
        with core.connect(db) as con:
            con.execute('INSERT INTO corrections VALUES(?,?,?,?,?,?,?,?,?)', (ident, member['tenant'], member['id'], offer['id'], catalog['version'], str(data.get('category', 'Other'))[:80], description, time.time(), 'pending review'))
        return reply({'id': ident, 'status': 'pending review'}, 201)
    return reply({'error': 'Not found.'}, 404)

def booking_request(request, action, member, ip):
    if request.method != 'POST':
        return reply({'error': 'Use POST.'}, 405)
    if not allowed('booking:' + ip, 60):
        return reply({'error': 'Too many requests. Wait one minute.'}, 429)
    db, data = database(), payload(request)
    if action in ('options', 'create', 'email-code', 'email-verify'):
        if not member:
            return reply({'error': 'Start a guest session first.'}, 401)
        if action == 'options':
            return reply(booking.options(db, str(data.get('clinic_id', '')), str(data.get('offer_id', ''))))
        if action == 'email-code':
            if not allowed('booking-email:' + ip, 3):
                return reply({'error': 'Too many email requests. Wait one minute.'}, 429)
            return reply(booking.request_patient_code(db, member['id'], data.get('email')))
        if action == 'email-verify':
            return reply(booking.verify_patient_code(db, member['id'], data.get('verification_id'), data.get('code')))
        if not allowed('booking-create:' + ip, 5):
            return reply({'error': 'Request limit reached. Wait one minute.'}, 429)
        return reply(booking.create(db, member['id'], data, settings.FRONTEND_ORIGIN), 201)
    ident = str(data.get('id', ''))
    if action == 'patient':
        return reply(booking.patient(db, ident, data.get('token', '')))
    if action == 'code':
        return reply(booking.issue_code(db, ident, data.get('token', '')))
    if action == 'verify':
        return reply(booking.verify_code(db, ident, data.get('token', ''), data.get('code', '')))
    if action in ('details', 'status'):
        return reply(booking.clinic_details(db, ident, data.get('session', ''), data.get('status') if action == 'status' else None))
    if action == 'lock':
        return reply(booking.lock_session(db, ident, data.get('session', '')))
    return reply({'error': 'Not found.'}, 404)
