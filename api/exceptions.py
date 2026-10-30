"""One safe JSON error contract for DRF, validation and domain failures."""
import logging
from django.core.exceptions import RequestDataTooBig
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import exception_handler
import accounts
import booking
import core

log = logging.getLogger(__name__)


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        details = response.data
        if isinstance(exc, ValidationError):
            response.data = {'error': 'Please check the highlighted fields.', 'details': details}
        else:
            message = details.get('detail', 'Request failed.') if isinstance(details, dict) else 'Request failed.'
            response.data = {'error': str(message)}
        return response
    if isinstance(exc, RequestDataTooBig):
        return Response({'error': 'Request too large.'}, status=413)
    if isinstance(exc, (accounts.AccountError, booking.BookingError)):
        return Response({'error': str(exc)}, status=exc.status)
    if isinstance(exc, (core.GuestAgentLimited, core.GuestCreationLimited)):
        data = {'error': str(exc), 'retry_after': exc.retry_after}
        if isinstance(exc, core.GuestAgentLimited):
            data.update(code='guest_preview_limit', reason=exc.reason)
        headers = {'Retry-After': str(exc.retry_after)} if exc.retry_after else {}
        return Response(data, status=429, headers=headers)
    if isinstance(exc, PermissionError):
        return Response({'error': 'Record unavailable.'}, status=404)
    if isinstance(exc, ValueError):
        return Response({'error': str(exc)}, status=400)
    if isinstance(exc, (TypeError, KeyError, AttributeError)):
        return Response({'error': 'Invalid request.'}, status=400)
    # Do not log exception values, request data, cookies or capability tokens.
    log.error('SelfPay API service request failed')
    return Response({'error': 'Service temporarily unavailable. Please retry.'}, status=503)
