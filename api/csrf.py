from django.http import JsonResponse
from django.views.csrf import csrf_failure

def failure(request,reason=''):
    if request.path.startswith('/api/center-pro/'):
        return JsonResponse({'detail':'Your sign-in session changed. Refresh verification and try again.','code':'csrf_failed'},status=403)
    return csrf_failure(request,reason=reason)
