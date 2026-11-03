# Center email portal

The frontend homepage has a Center login button. The Android app also includes the option on its homepage and a Center portal entry in its menu.

1. The center enters the email already stored on an active directory listing.
2. An unknown email receives a contact-support message; no email is sent.
3. A matching email receives a single-use six-digit SMTP code, valid for ten minutes.
4. Verification establishes a separate eight-hour HttpOnly center session. Patient and admin sessions are not changed.
5. The dashboard shows listings attached to the verified email and requests originally delivered to that same email for those listings. A shared center email can manage its linked listings.
6. Open request reveals the patient contact details. Approve, Contact attempted, and Decline update the existing booking status used by both patient and admin dashboards. Contacts are hidden once closed, following the existing request policy.

## AWS update

Use the existing BOOKING_ENCRYPTION_KEY and SMTP settings. Do not generate a replacement encryption key: existing requests must remain decryptable.

```bash
cd /var/www/MyselfPay_Backend
sudo git pull origin main
sudo ./venv/bin/python manage.py migrate --noinput
sudo ./venv/bin/python manage.py check
sudo systemctl restart myselfpay-backend.service
```

Migration 0009 creates center access tables and indexes historical request recipients from encrypted booking payloads. It must finish before restarting the updated API. If it reports a decryption error, restore the original booking encryption key before retrying. No new SMTP or Twilio credentials are required.

Email lookup uses the published directory email field, case-insensitively. Keep it current. Login never creates or edits listings. Removing or changing that email immediately revokes access on the next dashboard or request action. Unknown-email errors intentionally disclose directory membership to meet the requested UX.

OTP attempts are capped at five per challenge; a new code is required afterward. Codes are hashed, email addresses encrypted, sessions hashed, and request mutations require the separate center CSRF token plus trusted browser origin. No patient tracking links or clinic capability tokens are returned by the center portal.

Tests use synthetic addresses and mocked SMTP; they never send real email. Production SMTP delivery and the server migration require verification on AWS.
