"""DRF throttles backed by the existing shared PostgreSQL quota table."""
import hashlib
import math
import time
from django.conf import settings
from rest_framework.throttling import BaseThrottle
import core


class DatabaseWindowThrottle(BaseThrottle):
    scope = 'api'
    limit = 60

    def allow_request(self, request, view):
        now = time.time()
        self.retry_after = max(1, math.ceil(60 - now % 60))
        ip = request.META.get('REMOTE_ADDR', 'unknown')
        key = hashlib.sha256(f'{self.scope}:{ip}'.casefold().encode()).hexdigest()
        with core.connect(settings.DOMAIN_DATABASE) as con:
            row = con.execute(
                'INSERT INTO login_limits VALUES(?,?,1) ON CONFLICT(key,window_id) '
                'DO UPDATE SET attempts=login_limits.attempts+1 RETURNING attempts',
                (key, int(now // 60)),
            ).fetchone()
        return row['attempts'] <= self.limit

    def wait(self):
        return self.retry_after


class GuestThrottle(DatabaseWindowThrottle):
    scope = 'guest'
    limit = 20


class BookingThrottle(DatabaseWindowThrottle):
    scope = 'booking'


class BookingEmailThrottle(DatabaseWindowThrottle):
    scope = 'booking-email'
    limit = 3


class BookingCreateThrottle(DatabaseWindowThrottle):
    scope = 'booking-create'
    limit = 5
