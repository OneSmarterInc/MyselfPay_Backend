"""Single-use emailed codes for assigned center owners and staff."""
import hashlib,secrets
from datetime import timedelta
from email.message import EmailMessage
from django.conf import settings
from django.contrib.auth import get_user_model,login
from django.contrib.auth.hashers import make_password,check_password
from django.db import transaction
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_POST
from .auth_flows import allowed,payload
from .models import CenterLoginOTP,Membership
from .views import identity

def reply(data,status=200):return JsonResponse(data,status=status,headers={'Cache-Control':'no-store'})
def digest(value):return hashlib.sha256(value.encode()).hexdigest()
def has_access(user):return user.is_active and Membership.objects.filter(user=user,center__directory_active=True,role__in=['owner','staff']).exists()

@require_POST
@csrf_protect
def request_code(request):
    email=str(payload(request).get('email','')).strip().lower()[:254]
    if not allowed('center-otp-ip:'+request.META.get('REMOTE_ADDR',''),30) or not allowed('center-otp-email:'+email,5):
        return reply({'detail':'Please wait before requesting another code.'},429)
    challenge=secrets.token_urlsafe(32)
    result={'challenge':challenge,'detail':'If this email has center access, a six-digit code will be sent. Enter it below. The code expires in 10 minutes.'}
    from .access import directory_owner_access
    directory_owner_access(email)
    users=get_user_model().objects.filter(email__iexact=email,is_active=True)
    user=users.first() if '@' in email and users.count()==1 else None
    if not user or not has_access(user):return reply(result)
    code=f'{secrets.randbelow(1000000):06d}'
    with transaction.atomic():
        # Serialize issuance for this account and invalidate older unconsumed codes.
        get_user_model().objects.select_for_update().get(pk=user.pk)
        CenterLoginOTP.objects.filter(user=user,used_at__isnull=True).update(used_at=timezone.now())
        attempt=CenterLoginOTP.objects.create(user=user,challenge_digest=digest(challenge),code_digest=make_password(code),expires_at=timezone.now()+timedelta(minutes=10))
    if settings.DEBUG and getattr(settings,'CENTER_PRO_LOCAL_LINKS',False):
        result.update(development_code=code,detail='Local development code created. No email was sent.')
    else:
        import booking_mail
        message=EmailMessage();message['Subject']='Your MySelfPay center sign-in code'
        message.set_content('Your MySelfPay center sign-in code is: '+code+'\n\nEnter this code on the center sign-in page. It expires in 10 minutes and can be used once. Do not share this code.')
        try:booking_mail.send_message(message,email,smtp_only=True)
        except booking_mail.MailDeliveryUncertain:
            result['detail']='Sending could not be confirmed. Check your inbox and enter the code if it arrives, or request another code.'
        except booking_mail.MailUnavailable as error:
            attempt.delete();return reply({'detail':str(error)},503)
        except Exception:
            result['detail']='Email delivery could not be confirmed. Check your inbox and enter the code if it arrives, or request another code.'
    return reply(result)

@require_POST
@csrf_protect
@transaction.atomic
def verify_code(request):
    if not allowed('center-otp-verify-ip:'+request.META.get('REMOTE_ADDR',''),60):return reply({'detail':'Too many attempts. Please try again later.'},429)
    data=payload(request);challenge=str(data.get('challenge',''))[:256];code=str(data.get('code','')).strip()[:12]
    attempt=CenterLoginOTP.objects.select_for_update().select_related('user').filter(challenge_digest=digest(challenge),used_at__isnull=True,expires_at__gt=timezone.now(),attempts__lt=5).first()
    invalid={'detail':'The code is incorrect, expired, or no longer has center access. Request a new code if needed.'}
    if not attempt or not has_access(attempt.user):return reply(invalid,400)
    attempt.attempts+=1
    correct=len(code)==6 and code.isascii() and code.isdigit() and check_password(code,attempt.code_digest)
    if not correct:
        if attempt.attempts>=5:attempt.used_at=timezone.now()
        attempt.save(update_fields=['attempts','used_at']);return reply(invalid,400)
    attempt.used_at=timezone.now();attempt.save(update_fields=['attempts','used_at'])
    login(request,attempt.user,backend='django.contrib.auth.backends.ModelBackend')
    request.session.pop('admin_totp_verified',None);request.session.pop('admin_totp_password_at',None)
    request.session['center_pro_verified_user']=attempt.user_id
    return reply(identity(request))
