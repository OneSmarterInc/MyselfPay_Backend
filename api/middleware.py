from django.conf import settings
from django.http import JsonResponse
from django.core.exceptions import DisallowedHost
from django.middleware.gzip import GZipMiddleware


class DirectoryCompressionMiddleware(GZipMiddleware):
    """Compress large directory responses, never account/OTP/admin payloads."""
    def process_response(self, request, response):
        if request.path in ('/api/catalog', '/api/search', '/api/directory-summary') or request.path.startswith('/api/v1/spatial-bundle/'):
            return super().process_response(request, response)
        return response

class RequestSecurityMiddleware:
    """Require explicit trusted browser origin on every state-changing request.

    Authenticated mutations additionally require a constant-time session CSRF
    check in DRF session authentication. Origins do not substitute for authentication.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # ALB probes use a target-IP Host header. The public read-only health
        # endpoint is intentionally host-agnostic; all other routes validate Host.
        if request.path.rstrip('/') != '/api/health':
            try:
                request.get_host()
            except DisallowedHost:
                return JsonResponse({'error': 'Host rejected.'}, status=400)
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and request.headers.get('Origin') != settings.FRONTEND_ORIGIN:
            return JsonResponse({'error': 'Origin rejected.'}, status=403)
        if request.path == '/admin/login/' and request.method == 'POST':
            from api.throttles import AdminLoginThrottle
            if not AdminLoginThrottle().allow_request(request, None):
                return JsonResponse({'error': 'Too many admin login attempts. Try again in a minute.'}, status=429)
        response = self.get_response(request)
        response['Cache-Control'] = 'private, no-store' if request.path.startswith('/api/v1/spatial-bundle/') else 'no-store'
        response['X-Frame-Options'] = 'DENY'
        if request.path.startswith('/api/mailer/unsubscribe/'):
            response['Referrer-Policy'] = 'no-referrer'
        if request.path.startswith('/admin'):
            response['Referrer-Policy'] = 'same-origin'
        response['Content-Security-Policy'] = ("default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'self'" if request.path.startswith(('/admin', '/api/mailer/unsubscribe/')) else "default-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        return response
