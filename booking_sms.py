"""Twilio Verify adapter. Credentials never leave server-side requests."""
import base64
import json
import logging
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler


logger = logging.getLogger(__name__)


class SMSUnavailable(RuntimeError):
    def __init__(self, message, *, http_status=None, provider_code=None):
        super().__init__(message)
        self.http_status = http_status
        self.provider_code = provider_code


def error_code(error):
    try:
        value = json.loads(error.read(8192)).get('code')
        return int(value) if str(value).isdigit() and len(str(value)) <= 8 else None
    except (ValueError, AttributeError, OSError):
        return None


def configuration():
    from admin_settings import twilio_configuration
    try:
        values = twilio_configuration()
    except Exception:
        raise SMSUnavailable('Mobile verification is temporarily unavailable. Please contact support.') from None
    if not re.fullmatch(r'SK[0-9a-fA-F]{32}', values.get('api_key_sid', '')) or not values.get('api_key_secret') or not re.fullmatch(r'VA[0-9a-fA-F]{32}', values.get('verify_service_sid', '')):
        raise SMSUnavailable('Mobile verification is not available yet. Please contact support.')
    return values


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def request_verify(resource="", fields=None, method="POST"):
    values = configuration()
    url = 'https://verify.twilio.com/v2/Services/' + values['verify_service_sid'] + ('/' + resource if resource else '')
    auth = base64.b64encode((values['api_key_sid'] + ':' + values['api_key_secret']).encode()).decode()
    request = Request(url, data=urlencode(fields).encode() if fields is not None else None, method=method,
                      headers={'Authorization': 'Basic ' + auth, 'Content-Type': 'application/x-www-form-urlencoded'})
    try:
        with build_opener(NoRedirect()).open(request, timeout=12) as response:
            result = json.loads(response.read(65536))
    except HTTPError as exc:
        code = error_code(exc)
        logger.warning('Twilio Verify request failed: HTTP=%s TwilioCode=%s', exc.code, code or 'unknown')
        if exc.code == 429:
            raise SMSUnavailable('Too many SMS attempts. Please try again later.', http_status=exc.code, provider_code=code) from None
        if resource == 'VerificationCheck' and exc.code == 404:
            return {'status': 'expired'}
        raise SMSUnavailable('SMS verification could not be completed. Check the number or contact support.', http_status=exc.code, provider_code=code) from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise SMSUnavailable('SMS verification is temporarily unavailable. Please try again later.') from None
    return result


def post(resource, fields):
    return request_verify(resource, fields)


def check_service():
    return request_verify(method='GET')


def send_code(phone):
    result = post('Verifications', {'To': phone, 'Channel': 'sms'})
    sid = result.get('sid', '')
    if result.get('status') != 'pending' or not re.fullmatch(r'VE[0-9a-fA-F]{32}', sid):
        raise SMSUnavailable('The SMS code could not be sent. Please try again later.')
    return sid


def check_code(sid, code):
    return post('VerificationCheck', {'VerificationSid': sid, 'Code': code}).get('status') == 'approved'
