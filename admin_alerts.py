"""Tell administrators when a center sends a change that waits for approval."""
import logging
import hashlib
from django.core.cache import cache
from email.message import EmailMessage
import booking
import booking_mail

log = logging.getLogger(__name__)


def when(stamp):
    """A stamp as date and time in the server time zone, with the zone name."""
    if not stamp:
        return ''
    try:
        from datetime import datetime, timezone as tz
        from django.utils import timezone
        return timezone.localtime(datetime.fromtimestamp(float(stamp), tz.utc)).strftime('%b %d, %Y %I:%M %p %Z').replace(' 0', ' ', 1)
    except Exception:
        from datetime import datetime, timezone as tz
        return datetime.fromtimestamp(float(stamp), tz.utc).strftime('%b %d, %Y %I:%M %p UTC')


def pending_counts(path):
    """Waiting price submissions and profile edits, for the Admin menu."""
    key = 'admin:pending:' + hashlib.sha256(str(path).encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return cached
    try:
        with booking.connection(path) as db:
            prices = db.execute("SELECT COUNT(*) AS n FROM center_price_submissions WHERE status='pending'").fetchone()['n']
            profiles = db.execute("SELECT COUNT(*) AS n FROM center_profile_edits WHERE status='pending'").fetchone()['n']
        cache.set(key, (prices, profiles), timeout=15)
        return prices, profiles
    except Exception:
        return 0, 0


def admin_emails():
    try:
        from django.contrib.auth import get_user_model
        users = get_user_model().objects.filter(is_active=True, is_superuser=True).exclude(email='')
        return sorted({u.email.strip() for u in users if u.email and '\r' not in u.email and '\n' not in u.email})
    except Exception:
        return []


def notify(kind, center_name, detail):
    """Email every administrator. A mail failure never blocks the center's change."""
    try:
        page = 'Center prices' if kind == 'prices' else 'Center info'
        message = EmailMessage()
        message['Subject'] = f'MySelfPay: {center_name} sent {"price" if kind == "prices" else "profile"} updates for approval'
        message.set_content(f'{center_name} sent changes that wait for your approval.\n\n{detail}\n\n'
                            f'Open the Admin workspace and choose "{page}" to approve or reject.\n\n'
                            'MySelfPay by One Smarter, Inc.\nPlease do not reply to this automated email.')
        for address in admin_emails():
            try:
                booking_mail.send_message(message, address)
            except Exception:
                log.warning('Admin update notice was not sent to one recipient.')
    except Exception:
        log.warning('Admin update notice failed.')
