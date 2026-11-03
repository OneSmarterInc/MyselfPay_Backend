# Contact verification before booking

## Deploy

```bash
cd /var/www/MyselfPay_Backend
git pull --ff-only origin main
./venv/bin/python manage.py migrate --noinput
./venv/bin/python manage.py check
sudo systemctl restart myselfpay-backend.service
```

Migration 0006 adds booking_phone_checks and booking_sms_limits without changing existing booking records. Update the backend before using the new frontend.

In MySelfPay Admin > Twilio settings, save the API Key SID (SK), API key secret, and Verify Service SID (VA). Configure the Verify service for six-digit SMS codes and the required destination countries. Environment fallbacks remain available (TWILIO_API_KEY_SID / SID, TWILIO_API_KEY_SECRET / CLIENTSECERT, TWILIO_VERIFY_SERVICE_SID). Email codes continue to use the existing SMTP settings. No additional Python package is needed; the transport uses the standard library over HTTPS.

Book appointment first opens email/mobile verification. Each contact has its own send and verify controls. The time/exam form opens only after both are verified. Editing a contact invalidates its proof; expired proofs return the user to verification. Mobile numbers must include the country prefix, e.g. +1 or +91. Booking notifications use the verified contact details entered for that request.

Backend phone-code / phone-verify endpoints require an authenticated portal account, origin and CSRF checks. Codes are checked by Twilio Verify, never logged or stored locally. Phones are encrypted at rest; quotas use hashes. Local proofs are owner-bound, number-bound, expire after 15 minutes, and are consumed transactionally alongside email proofs. Sending is limited to 5 per owner/number per hour and 500 per day globally; resend cooldown is 60 seconds. Failed check attempts are limited to 5. Provider failures fail closed. Tests mock Twilio and SMTP; no live messages are sent by the automated suite.
