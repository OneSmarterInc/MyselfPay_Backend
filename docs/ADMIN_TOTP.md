# Admin-only authenticator login

The Django MySelfPay admin now requires password plus an authenticator code.
Patient mobile OTP and center email OTP authentication are unchanged.

On first successful password login, scan the locally generated QR code in Google
Authenticator, Microsoft Authenticator, or another TOTP app. Alternatively enter
the setup key manually (time based, six digits, 30-second period). Confirm one
code to enable the authenticator and save the eight displayed recovery codes.
No third-party QR service receives the setup secret.

Every new Django admin login requires a code. Existing admin sessions without
TOTP proof must sign in again. Enrollment requires a password entered in the
previous ten minutes. Five incorrect attempts lock verification for fifteen
minutes across sessions. Accepted counters cannot be reused. Recovery codes are
hashed, consumed atomically, and shown once. TOTP seeds are encrypted with a key
derived from DJANGO_SECRET_KEY. Keep this server secret stable and backed up.

Deploy dependencies and migration before restarting the backend:

```bash
cd /var/www/MyselfPay_Backend
sudo git pull --ff-only origin main
sudo ./venv/bin/python -m pip install -r requirements.txt
sudo ./venv/bin/python manage.py migrate --noinput
sudo ./venv/bin/python manage.py collectstatic --noinput
sudo ./venv/bin/python manage.py check
sudo systemctl restart myselfpay-backend.service
```

Use the existing createsuperuser account at /admin/. No frontend deployment or
new environment variables are needed. This adds migration 0021_admin_totp.

If an administrator loses both their authenticator and recovery codes, an
authorized server operator can reset that specific credential:

```bash
sudo ./venv/bin/python manage.py reset_admin_totp ADMIN_USERNAME --confirm
```

This revokes existing TOTP proofs and requires password login plus fresh
enrollment. The account's password is unchanged. There is no public reset or
disable endpoint. Server time must be synchronized for authenticator codes.
