from django.conf import settings
from django.http import JsonResponse
from django.core.exceptions import DisallowedHost

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
        response = self.get_response(request)
        response['Cache-Control'] = 'no-store'
        response['X-Frame-Options'] = 'DENY'
        response['Content-Security-Policy'] = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        return response
