from django.conf import settings
from django.contrib.auth import get_user_model
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.contrib.auth.models import AnonymousUser
from rest_framework.authentication import BaseAuthentication, SessionAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied
import booking, center_portal, admin_totp


def admin_verified(request):
    raw=getattr(request, '_request', request)
    return getattr(raw,'_center_pro_auth_kind','')!='center' and admin_totp.verified(raw)


def center_cookie():
    return 'selfpay_center' if settings.DEBUG else '__Host-selfpay_center'


def principal(request, write=False):
    request=getattr(request, '_request', request)
    user=request.user
    workspace=request.GET.get('workspace','auto')
    if user and user.is_authenticated and user.is_active:
        if workspace!='center' and user.is_superuser and admin_totp.verified(request):
            if write: SessionAuthentication().enforce_csrf(request)
            request._center_pro_auth_kind='admin'
            return user
        if workspace!='admin' and request.session.get('center_pro_verified_user')==user.pk:
            if write: SessionAuthentication().enforce_csrf(request)
            request._center_pro_auth_kind='center'
            return user
        if workspace=='center' and user.is_superuser and admin_totp.verified(request) and user.membership_set.filter(center__directory_active=True).exists():
            if write: SessionAuthentication().enforce_csrf(request)
            request._center_pro_auth_kind='center'
            return user
    if workspace=='admin':return AnonymousUser()
    token=request.COOKIES.get(center_cookie(), '')
    if not token: return AnonymousUser()
    try:
        with booking.connection(settings.DOMAIN_DATABASE) as db:
            email,listings,_=center_portal.identity(db,token,request.headers.get('X-Center-CSRF','') if write else None)
        users=get_user_model().objects.filter(email__iexact=email,is_active=True)
        if users.count()!=1:return AnonymousUser()
        user=users.first()
        # Memberships are assigned by the existing administrator, never from mailbox possession.
        if not user.membership_set.filter(center__directory_active=True).exists():return AnonymousUser()
        request._center_pro_auth_kind='center'
        return user
    except booking.BookingError as error:
        raise AuthenticationFailed(str(error)) from None


class IntegratedAuthentication(BaseAuthentication):
    def authenticate(self,request):
        user=principal(request,request.method not in ('GET','HEAD','OPTIONS'))
        return (user,None) if user.is_authenticated else None
    def authenticate_header(self,request):return 'CenterSession'


def session_payload(request):
    from .models import Membership
    from rest_framework.exceptions import APIException
    try:user=principal(request)
    except APIException:user=AnonymousUser()
    memberships=Membership.objects.filter(user=user,center__directory_active=True).select_related('center') if user.is_authenticated else []
    center_csrf=''
    if request.COOKIES.get(center_cookie()):
        try:
            with booking.connection(settings.DOMAIN_DATABASE) as db:
                _,_,center_csrf=center_portal.identity(db,request.COOKIES[center_cookie()])
        except booking.BookingError:pass
    return {'user':{'name':user.first_name or user.username,'is_admin':bool(user.is_superuser and admin_verified(request))} if user.is_authenticated else None,
      'centers':[{'id':m.center_id,'name':m.center.name,'timezone':m.center.timezone,'role':m.role} for m in memberships],
      'can_admin':bool(request.user and request.user.is_authenticated and request.user.is_superuser and admin_totp.verified(request)),
      'csrf_token':get_token(request),'center_csrf':center_csrf,'center_verified':bool(center_csrf),'development_login':False,'development':settings.DEBUG}


def owner_portal_token(request, center_id):
    """Bridge a verified owner session to one legacy listing, never an email-wide grant."""
    import secrets
    from django.core import signing
    from django.shortcuts import get_object_or_404
    from .models import Membership
    user = principal(request, write=request.method not in ('GET','HEAD','OPTIONS'))
    if not user or not user.is_authenticated:
        raise AuthenticationFailed('Sign in with your assigned center email.')
    member = get_object_or_404(Membership.objects.select_related('center'), user=user, center_id=center_id, role='owner', center__directory_active=True)
    raw = getattr(request,'_request',request)
    csrf = raw.session.get('center_owner_portal_csrf')
    if not csrf:
        csrf = secrets.token_urlsafe(32)
        raw.session['center_owner_portal_csrf'] = csrf
    cache_key = 'owner_portal_token_' + str(member.center_id)
    token = raw.session.get(cache_key)
    if token:
        try:
            cached = signing.loads(token.split(':',1)[1],salt='center-owner-portal',max_age=28800)
            if cached.get('user') == user.pk and cached.get('csrf') == csrf:
                return token
        except signing.BadSignature:
            pass
    token = 'owner-session:' + signing.dumps({'user':user.pk,'center':member.center_id,'csrf':csrf},salt='center-owner-portal')
    raw.session[cache_key] = token
    return token


def owner_portal_identity(db, token, csrf=None):
    import json, hmac
    from django.core import signing
    from .models import Membership
    try:
        value = signing.loads(token.split(':',1)[1],salt='center-owner-portal',max_age=28800)
        member = Membership.objects.select_related('user','center').get(user_id=value['user'],center_id=value['center'],role='owner',user__is_active=True,center__directory_active=True)
    except (signing.BadSignature,Membership.DoesNotExist,KeyError,ValueError):
        raise booking.BookingError('Owner access has expired or was removed.',403)
    if csrf is not None and not hmac.compare_digest(str(csrf),value['csrf']):
        raise booking.BookingError('Refresh this page before trying again.',403)
    from directory_policy import visible_sql
    predicate,values = visible_sql()
    row = db.execute('SELECT entry_key,payload FROM entries WHERE batch_id=? AND entry_key=? AND '+predicate,( __import__('core').active_batch(db),member.center.directory_key,*values)).fetchone()
    if not row:
        raise booking.BookingError('This listing is no longer available.',404)
    item=json.loads(row['payload'])
    item['zip']=center_portal.saved_zip(item)
    listing={'id':row['entry_key'],**{k:item.get(k,'') for k in ('name','area','states','address','zip','zip_code','phone','email','source','coverage','entity','mri','ct','other','is_test_center')}}
    return str(item.get('email') or member.user.email).strip().lower(),[listing],value['csrf']


class OwnerPortalAuthentication(IntegratedAuthentication):
    def authenticate(self, request):
        if 'owner_center' not in request.query_params:
            return None
        return super().authenticate(request)
