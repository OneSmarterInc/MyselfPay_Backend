"""Server-only encrypted settings. No secrets are returned to portal clients."""
import base64
import hashlib
import os
from cryptography.fernet import Fernet
from django.conf import settings


def secret_cipher(purpose='twilio'):
    key = hashlib.sha256(('selfpay-admin-' + purpose + '-v1:' + settings.SECRET_KEY).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def twilio_configuration():
    from api.models import TwilioConfiguration
    row = TwilioConfiguration.objects.filter(pk=1).first()
    return {
        'api_key_sid': (row.api_key_sid if row else '') or os.getenv('TWILIO_API_KEY_SID', os.getenv('SID', '')),
        'api_key_secret': (secret_cipher().decrypt(row.secret_encrypted.encode()).decode() if row and row.secret_encrypted else '') or os.getenv('TWILIO_API_KEY_SECRET', os.getenv('CLIENTSECERT', '')),
        'account_sid': (row.account_sid if row else '') or os.getenv('TWILIO_ACCOUNT_SID', ''),
        'verify_service_sid': (row.verify_service_sid if row else '') or os.getenv('TWILIO_VERIFY_SERVICE_SID', ''),
    }


def smtp_configuration():
    """Resolve one complete SMTP profile per send; never mix saved/environment secrets."""
    from django.apps import apps
    if settings.configured and apps.ready:
        from api.models import SMTPConfiguration
        row = SMTPConfiguration.objects.filter(pk=1, use_database=True).first()
        if row:
            return {'source': 'admin', 'host': row.host, 'port': row.port, 'username': row.username,
                    'password': secret_cipher('smtp').decrypt(row.password_encrypted.encode()).decode() if row.password_encrypted else '',
                    'sender': row.from_email, 'name': row.from_name, 'ssl': row.security == 'ssl',
                    'tls': row.security == 'starttls', 'booking_enabled': row.booking_email_enabled,
                    'allowed_recipients': row.allowed_recipients, 'test_recipient': row.test_recipient}
    use_ssl = os.getenv('SMTP_USE_SSL', 'false').lower() == 'true'
    return {'source': 'environment', 'host': os.getenv('SMTP_HOST', ''),
            'port': os.getenv('SMTP_PORT', '465' if use_ssl else '587'),
            'username': os.getenv('SMTP_USERNAME', ''), 'password': os.getenv('SMTP_PASSWORD', ''),
            'sender': os.getenv('SMTP_FROM', os.getenv('SMTP_USERNAME', '')), 'name': os.getenv('SMTP_FROM_NAME', 'MySelfPay'),
            'ssl': use_ssl, 'tls': os.getenv('SMTP_USE_TLS', 'true').lower() == 'true',
            'booking_enabled': os.getenv('BOOKING_EMAIL_ENABLED', 'false').lower() == 'true',
            'allowed_recipients': os.getenv('BOOKING_EMAIL_ALLOWED_RECIPIENTS', ''),
            'test_recipient': os.getenv('TEST_CENTER_EMAIL', '')}
