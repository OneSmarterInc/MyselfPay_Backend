"""Test helper: sign an administrator in with the authenticator step already passed."""
from api.models import AdminTOTP


def verified_login(client, user):
    """force_login plus a verified TOTP device, matching what admin_totp.verified() checks."""
    device, _ = AdminTOTP.objects.get_or_create(user=user, defaults={'secret_encrypted': 'synthetic', 'enabled': True})
    client.force_login(user)
    session = client.session
    session['admin_totp_verified'] = str(device.generation)
    session.save()
