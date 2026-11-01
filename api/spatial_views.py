from django.conf import settings
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from api.views import ResourceView
from api.throttles import DatabaseWindowThrottle
import hashlib
import time
import core
import spatial


class SpatialBundleThrottle(DatabaseWindowThrottle):
    def allow_request(self, request, view):
        now = time.time()
        self.retry_after = max(1, int(60 - now % 60))
        key = hashlib.sha256(('spatial:' + str(request.auth['id'])).encode()).hexdigest()
        with core.connect(settings.DOMAIN_DATABASE) as db:
            row = db.execute('INSERT INTO login_limits VALUES(?,?,1) ON CONFLICT(key,window_id) DO UPDATE SET attempts=login_limits.attempts+1 RETURNING attempts', (key, int(now // 60))).fetchone()
        return row['attempts'] <= 60


class SpatialBundleView(ResourceView):
    throttle_classes = [SpatialBundleThrottle]

    def get(self, request, h3_res7):
        if request.query_params:
            raise ValidationError('This endpoint accepts only a Resolution 7 cell in the URL; no coordinate parameters.')
        try:
            data = spatial.bundle(settings.DOMAIN_DATABASE, h3_res7)
        except spatial.SpatialNotReady:
            return Response({'error': 'Location indexing is not ready. Use ZIP or state search while the directory is indexed.'}, status=503, headers={'Retry-After': '60', 'Cache-Control': 'private, no-store'})
        return Response(data, headers={'Cache-Control': 'private, no-store', 'Vary': 'Cookie, Origin'})
