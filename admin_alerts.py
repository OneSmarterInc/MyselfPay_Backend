"""Tell administrators when a center sends a change that waits for approval."""
import logging
from email.message import EmailMessage
import booking
import booking_mail

log = logging.getLogger(__name__)


def pending_counts(path):
    """Waiting price submissions and profile edits, for the Admin menu."""
    try:
        with booking.connection(path) as db:
            prices = db.execute("SELECT COUNT(*) AS n FROM center_price_submissions WHERE status='pending'").fetchone()['n']
            profiles = db.execute("SELECT COUNT(*) AS n FROM center_profile_edits WHERE status='pending'").fetchone()['n']
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
