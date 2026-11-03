"""Server-only encrypted settings. No secrets are returned to portal clients."""
import base64
import hashlib
import os
from cryptography.fernet import Fernet
from django.conf import settings


def secret_cipher():
    key = hashlib.sha256(('selfpay-admin-twilio-v1:' + settings.SECRET_KEY).encode()).digest()
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
