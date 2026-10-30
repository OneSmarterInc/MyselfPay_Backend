"""DRF authentication for existing hashed, eight-hour SelfPay sessions."""
import secrets
from dataclasses import dataclass
from django.conf import settings
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission, SAFE_METHODS
import core


@dataclass(frozen=True)
class MemberIdentity:
    member: dict

    @property
    def is_authenticated(self):
        return True

    @property
    def pk(self):
        return self.member['id']


class SelfPaySessionAuthentication(BaseAuthentication):
    def authenticate(self, request):
        token = request.COOKIES.get(settings.SESSION_COOKIE_NAME, '')
        if not token:
            return None
        member = core.session(token, settings.DOMAIN_DATABASE)
        if not member:
            return None
        if request.method not in SAFE_METHODS and not secrets.compare_digest(
            request.headers.get('X-CSRF-Token', ''), member['csrf']
        ):
            raise PermissionDenied('CSRF token required.')
        return MemberIdentity(member), member

    def authenticate_header(self, request):
        return 'SelfPaySession'


class AccountMemberOnly(BasePermission):
    message = 'Sign in to an account to use saved account features.'

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated and request.auth['tenant'] != 'guest')
