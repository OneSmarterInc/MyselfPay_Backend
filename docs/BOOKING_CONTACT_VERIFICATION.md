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

Backend phone-code / phone-verify endpoints require an authenticated portal account, origin and CSRF checks. Codes are checked by Twilio Verify, never logged or stored locally. Phones are encrypted at rest. Local proofs are owner-bound, number-bound, expire after 15 minutes, and are consumed transactionally alongside email proofs. The application applies no sending, resend-cooldown or failed-check attempt limits to patient email/mobile OTP endpoints. Existing quota records are ignored. Twilio and SMTP provider restrictions still apply. Authentication, CSRF, code validity, expiration and single-use proofs remain enforced. Provider failures fail closed. Tests mock Twilio and SMTP; no live messages are sent by the automated suite.

## Diagnose an SMS failure

Run `./venv/bin/python manage.py check_twilio` on the server. It checks authentication and service access without sending an SMS or printing credentials. To test SMS acceptance explicitly, run `./venv/bin/python manage.py check_twilio --send --phone YOUR_INTERNATIONAL_NUMBER`. This sends one SMS verification request and prints a safe HTTP status / numeric Twilio error code if rejected. The diagnostic SMS is not a booking verification challenge; use the portal to complete booking verification afterward.

Application logs also contain `Twilio Verify request failed: HTTP=... TwilioCode=...`. Phone numbers, OTPs, authorization headers and provider message bodies are not logged by this diagnostic. Never paste credentials when sharing diagnostic output.
