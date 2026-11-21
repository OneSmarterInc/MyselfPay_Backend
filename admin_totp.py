"""Second factor restricted to Django's MySelfPay administrator sessions."""
import io
import re
import secrets
import time
from datetime import timedelta
from urllib.parse import urlsplit

import pyotp
import qrcode
from qrcode.image.svg import SvgPathImage
from django.contrib.auth import logout
from django.contrib.auth.hashers import make_password, check_password
from django.db import transaction
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.utils.safestring import mark_safe
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.debug import sensitive_post_parameters

from admin_settings import secret_cipher
from api.models import AdminTOTP


def verified(request):
    if not (request.user.is_authenticated and request.user.is_active and request.user.is_superuser):
        return False
    proof = request.session.get('admin_totp_verified', '')
    identity = (request.user.pk, proof)
    cached = getattr(request, '_admin_totp_permission', None)
    if cached is not None and cached[0] == identity:
        return cached[1]
    generation = AdminTOTP.objects.filter(user=request.user, enabled=True).values_list('generation', flat=True).first()
    result = bool(generation and constant_time_compare(str(generation), proof))
    # Never cache across requests: credential resets take effect on the next load.
    request._admin_totp_permission = (identity, result)
    return result


def safe_next(request, value):
    return value if value and url_has_allowed_host_and_scheme(value, {request.get_host()}, require_https=request.is_secure()) and urlsplit(value).path != reverse('admin:totp') else reverse('admin:index')


def password_complete(request, response):
    if request.method == 'POST' and response.status_code in (302, 303) and request.user.is_authenticated:
        request.session.pop('admin_totp_verified', None)
        request.session['admin_totp_password_at'] = time.time()
        request.session['admin_totp_next'] = safe_next(request, request.POST.get('next'))
        return redirect('admin:totp')
    return response


@transaction.atomic
def verify_code(user, code):
    device = AdminTOTP.objects.select_for_update().get(user=user)
    now = timezone.now()
    if device.locked_until and device.locked_until > now:
        return False, 'Too many attempts. Please wait 15 minutes before trying again.', None
    if device.locked_until:
        device.failed_attempts = 0
        device.locked_until = None
    valid, counter = False, None
    if re.fullmatch(r'[0-9]{6}', code):
        seed = secret_cipher('admin-totp').decrypt(device.secret_encrypted.encode()).decode()
        totp = pyotp.TOTP(seed)
        current = int(time.time() // 30)
        for step in (current - 1, current, current + 1):
            if step > device.last_counter and constant_time_compare(totp.at(step * 30), code):
                valid, counter = True, step
                break
    elif device.enabled and re.fullmatch(r'[a-f0-9]{20}', code):
        for index, saved in enumerate(device.recovery_hashes):
            if check_password(code, saved):
                device.recovery_hashes = device.recovery_hashes[:index] + device.recovery_hashes[index + 1:]
                valid = True
                break
    recovery = None
    if valid:
        if counter is not None:
            device.last_counter = counter
        if not device.enabled:
            recovery = [secrets.token_hex(10) for _ in range(8)]
            device.recovery_hashes = [make_password(value) for value in recovery]
            device.enabled = True
        device.failed_attempts = 0
        device.locked_until = None
    else:
        device.failed_attempts += 1
        if device.failed_attempts >= 5:
            device.locked_until = now + timedelta(minutes=15)
    device.save()
    return valid, '' if valid else 'The code is incorrect, expired, or already used. Try the next code.', (str(device.generation), recovery) if valid else None


@never_cache
@csrf_protect
@sensitive_post_parameters('code')
def challenge(request):
    if not (request.user.is_authenticated and request.user.is_active and request.user.is_superuser):
        return redirect('admin:login')
    if verified(request):
        return redirect(safe_next(request, request.session.pop('admin_totp_next', '')))
    password_at = request.session.get('admin_totp_password_at', 0)
    if not isinstance(password_at, (float, int)) or not 0 <= time.time() - password_at < 600:
        logout(request)
        return redirect('admin:login')
    device, _ = AdminTOTP.objects.get_or_create(user=request.user, defaults={
        'secret_encrypted': secret_cipher('admin-totp').encrypt(pyotp.random_base32().encode()).decode()})
    error = ''
    if request.method == 'POST':
        try:
            ok, error, result = verify_code(request.user, request.POST.get('code', '').strip())
        except Exception:
            # Keep access closed if the encryption key or credential is damaged.
            ok, error, result = False, 'Verification is unavailable. Contact your server administrator.', None
        if ok:
            generation, recovery = result
            request.session.cycle_key()
            request.session['admin_totp_verified'] = generation
            request.session.pop('admin_totp_password_at', None)
            if recovery:
                response = TemplateResponse(request, 'admin/totp.html', {
                    'title': 'Save your recovery codes', 'recovery_codes': recovery,
                    'continue_url': safe_next(request, request.session.pop('admin_totp_next', ''))})
                response['Referrer-Policy'] = 'no-referrer'
                return response
            return redirect(safe_next(request, request.session.pop('admin_totp_next', '')))
    seed, qr = '', ''
    if not device.enabled:
        try:
            seed = secret_cipher('admin-totp').decrypt(device.secret_encrypted.encode()).decode()
            uri = pyotp.TOTP(seed).provisioning_uri(request.user.get_username(), issuer_name='MySelfPay Admin')
            output = io.BytesIO()
            qrcode.make(uri, image_factory=SvgPathImage).save(output)
            qr = mark_safe(output.getvalue().decode())
        except Exception:
            error = 'Enrollment is unavailable. Contact your server administrator.'
    response = TemplateResponse(request, 'admin/totp.html', {
        'title': 'Verify administrator access' if device.enabled else 'Set up your authenticator',
        'enrolling': not device.enabled, 'setup_key': seed, 'qr': qr, 'error': error})
    response['Referrer-Policy'] = 'no-referrer'
    return response
