import base64
import hashlib
import json
import secrets
import time
from datetime import timedelta
from cryptography.fernet import Fernet
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model, login
from django.core.mail import send_mail
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
import pyotp
from .models import MagicLink, Membership, AuthLimit, AdminMFA
from .views import identity


def cipher():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256((settings.SECRET_KEY+'center-pro-mfa').encode()).digest()))


def payload(request):
    try:
        value=json.loads(request.body)
        return value if isinstance(value,dict) else {}
    except (ValueError,UnicodeDecodeError):
        return {}


@transaction.atomic
def allowed(key,maximum):
    now=timezone.now()
    item,_=AuthLimit.objects.get_or_create(key=hashlib.sha256(key.encode()).hexdigest(),defaults={'resets_at':now+timedelta(minutes=15)})
    item=AuthLimit.objects.select_for_update().get(pk=item.pk)
    if item.resets_at<=now:
        item.count=0;item.resets_at=now+timedelta(minutes=15)
    item.count+=1
    item.save(update_fields=['count','resets_at'])
    return item.count<=maximum


@require_POST
@csrf_protect
def request_link(request):
    email=str(payload(request).get('email','')).strip().lower()[:254]
    if not allowed('magic-ip:'+request.META.get('REMOTE_ADDR',''),30) or not allowed('magic-email:'+email,5):
        return JsonResponse({'detail':'Please wait before requesting another link.'},status=429)
    result={'detail':'If this email has center access, a sign-in link will be sent.'}
    users=get_user_model().objects.filter(email__iexact=email,is_active=True)
    if '@' not in email or users.count()!=1:
        return JsonResponse(result)
    user=users.first()
    if not Membership.objects.filter(user=user).exists():
        return JsonResponse(result)
    raw=secrets.token_urlsafe(32)
    link=MagicLink.objects.create(user=user,digest=hashlib.sha256(raw.encode()).hexdigest(),expires_at=timezone.now()+timedelta(minutes=15))
    url=settings.FRONTEND_ORIGIN+'/#center-pro/verify/'+raw
    if settings.DEBUG and getattr(settings,'CENTER_PRO_LOCAL_LINKS',False):
        result['development_url']=url
        result['detail']='Local development link created. No email was sent.'
    else:
        try:
            from email.message import EmailMessage
            import booking_mail
            message=EmailMessage();message['Subject']='Your MySelfPay center sign-in link'
            message.set_content('Open this single-use link within 15 minutes: '+url)
            booking_mail.send_message(message,email)
        except booking_mail.MailDeliveryUncertain:
            return JsonResponse({'detail':'The mail server did not confirm sending. Check your inbox before requesting another link.'},status=503)
        except booking_mail.MailUnavailable as error:
            link.delete()
            return JsonResponse({'detail':str(error)},status=503)
        except Exception:
            # Keep a valid short-lived link in case SMTP accepted before a logging failure.
            return JsonResponse({'detail':'Email delivery could not be confirmed. Check your inbox before retrying.'},status=503)
    return JsonResponse(result)


@require_POST
@csrf_protect
@transaction.atomic
def verify_link(request):
    raw=str(payload(request).get('token',''))[:256]
    link=MagicLink.objects.select_for_update().select_related('user').filter(digest=hashlib.sha256(raw.encode()).hexdigest(),used_at__isnull=True,expires_at__gt=timezone.now()).first()
    if not link or not link.user.is_active or not Membership.objects.filter(user=link.user).exists():
        return JsonResponse({'detail':'This sign-in link is invalid, expired or no longer has access.'},status=400)
    link.used_at=timezone.now();link.save(update_fields=['used_at'])
    login(request,link.user,backend='django.contrib.auth.backends.ModelBackend')
    request.session.pop('admin_totp_verified',None)
    request.session.pop('admin_totp_password_at',None)
    request.session['center_pro_verified_user']=link.user_id
    return JsonResponse(identity(request))


@require_POST
@csrf_protect
def admin_login(request):
    return JsonResponse({'detail':'Use the existing MySelfPay administrator login.','login_url':'/admin/login/?next=/admin/center-pro/'},status=403)
