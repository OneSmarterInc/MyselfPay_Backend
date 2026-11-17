"""Admin pages and signals for the audit trail."""
import csv
import os
from datetime import datetime, timezone
from django.contrib import messages
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.http import HttpResponse
from django.shortcuts import redirect
from django.template.response import TemplateResponse
import admin_alerts
import audit

SPREADSHEET_RISK = ('=', '+', '-', '@')


def wrap(view):
    """Record every Admin page load and change with who, when and from where."""
    def audited(request, *args, **kwargs):
        audit.bind(request, 'admin', request.user.get_username())
        try:
            response = view(request, *args, **kwargs)
        except Exception as exc:
            audit.log('admin.error', target_type='page', target_id=request.path, result='error', detail={'error': type(exc).__name__})
            audit.unbind()
            raise
        status = getattr(response, 'status_code', 200)
        audit.log('admin.change' if request.method == 'POST' else 'admin.view', target_type='page', target_id=request.path,
                  result='ok' if status < 400 else 'denied', detail={'method': request.method, 'status': status})
        audit.unbind()
        return response
    return audited


def _day(request, name, end=False):
    try:
        value = datetime.strptime(request.GET.get(name, ''), '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()
        return value + 86400 if end else value
    except ValueError:
        return 0.0


def _safe(value):
    text = str(value)
    return "'" + text if text.startswith(SPREADSHEET_RISK) else text


def page(site, request):
    if request.method == 'POST':
        configured = bool(audit.url())
        checked, bad, why = audit.verify_chain() if configured else (0, None, 'The audit store is not configured.')
        good = configured and bad is None
        audit.log('admin.audit.verify', result='ok' if good else 'error', new=f'{checked} rows checked', detail={'first_bad': bad, 'why': why})
        if good:
            messages.success(request, f'Chain intact: {checked} rows checked.')
        else:
            messages.error(request, f'Chain problem at row {bad}: {why}' if configured else why)
        return redirect('admin:audit_log')
    filters = {'actor': request.GET.get('actor', '').strip(), 'action': request.GET.get('action', '').strip(),
               'scope': request.GET.get('scope', '').strip(), 'text': request.GET.get('text', '').strip(),
               'since': _day(request, 'from'), 'until': _day(request, 'to', True)}
    if request.GET.get('export') == 'csv':
        rows = audit.search(**filters, limit=5000)
        audit.log('admin.audit.export', target_type='audit', new=f'{len(rows)} rows', detail={'filters': {k: v for k, v in filters.items() if v}})
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="audit-log.csv"'
        writer = csv.writer(response)
        writer.writerow(list(audit.FIELDS) + ['prev_hash', 'row_hash'])
        for r in rows:
            writer.writerow([_safe(r[k]) for k in audit.FIELDS] + [r['prev_hash'], r['row_hash']])
        return response
    try:
        offset = max(0, int(request.GET.get('offset', '0')))
    except ValueError:
        offset = 0
    rows = audit.search(**filters, limit=100, offset=offset)
    for r in rows:
        r['when'] = admin_alerts.when(r['ts'])
    ok, why = audit.healthy()
    context = {**site.each_context(request), 'title': 'Audit log', 'rows': rows, 'offset': offset,
               'next_offset': offset + 100 if len(rows) == 100 else None, 'prev_offset': max(0, offset - 100) if offset else None,
               'store_ok': ok, 'store_why': why, 'mode': audit.mode(), 'proxies': os.environ.get('AUDIT_TRUSTED_PROXIES', '0'),
               'seen_ip': audit.client_ip(request), 'seen_remote': request.META.get('REMOTE_ADDR', ''),
               'seen_forwarded': request.META.get('HTTP_X_FORWARDED_FOR', ''), 'q': request.GET}
    return TemplateResponse(request, 'admin/audit_log.html', context)


def _on_login(sender, request, user, **kwargs):
    audit.bind(request, 'admin', user.get_username())
    audit.log('admin.login', target_type='account', target_id=user.get_username())
    audit.unbind()


def _on_logout(sender, request, user, **kwargs):
    if user is not None:
        audit.bind(request, 'admin', user.get_username())
        audit.log('admin.logout', target_type='account', target_id=user.get_username())
        audit.unbind()


def _on_failed(sender, credentials, request=None, **kwargs):
    if request is not None:
        audit.bind(request, 'admin', str(credentials.get('username', ''))[:150])
    audit.log('admin.login.failed', result='denied', target_type='account', target_id=str(credentials.get('username', ''))[:150],
              actor=str(credentials.get('username', ''))[:150], actor_type='admin')
    audit.unbind()


user_logged_in.connect(_on_login, dispatch_uid='audit_login')
user_logged_out.connect(_on_logout, dispatch_uid='audit_logout')
user_login_failed.connect(_on_failed, dispatch_uid='audit_login_failed')
