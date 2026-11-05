# Patient accounts: username and verified mobile

This flow supersedes patient email registration, booking email verification and patient Secure Mail links. Center email login, center OTP verification and center notification emails remain supported.

## Behavior

- Register with a chosen username, name, password, country code/mobile number and optional five-digit ZIP.
- A real Twilio Verify approval is required before registration succeeds. Challenges are bound to the mobile number and consumed once when the account is saved. Failed or expired checks cannot create accounts.
- Log in using username/password. Usernames are case insensitive and unique; a verified mobile number belongs to one account.
- A logged-in, verified account can request times directly. The server takes the contact mobile from the saved account, rather than trusting booking form fields. No patient email or email proof is needed or saved in new requests.
- With SECURE_MAIL_ENABLED=true, new booking notifications go only to the center through the signed Secure Mail integration (provider-only thread). With the integration disabled, existing direct SMTP delivery remains available. The center must verify its own email before opening and approving the request. Patient and admin dashboards retain request status updates.
- Patient screens never offer Secure Mail or replies. New Secure Mail jobs contain a single provider participant and an encrypted approval action; no patient email or mailbox is created. Historical two-party jobs remain undispatched. The center verifies its email in Secure Mail, then uses the approval action to update MySelfPay status. Already delivered center request links still require center verification.

## Existing accounts

No account or request is deleted by migration `0013`. Existing signed-in accounts can still complete the account-setup prompt. The public login page no longer displays the legacy setup link. Contact support if an older account is signed out and does not yet have a username.

Choose a username, enter the existing password and verify the saved mobile number. A signed-in legacy patient may verify a replacement mobile while proving the existing password. Setup keeps the same member ID, profile, ZIP, preferences and request ownership, and revokes previous sessions. The old account email is replaced by an opaque internal identifier in the legacy identity columns. Old encrypted booking records and historical verification records are retained; they are not used for new patient authentication, contact or notifications.

If the account has no saved mobile, its saved number lacks a country code, multiple old accounts share that number, or the patient cannot access it, support must resolve the account first. An administrator can update an **unconverted** account's mobile and reset its password in Registered users. The patient then completes OTP setup. Admin edits cannot silently replace an already verified mobile.

## Deployment

Deploy this backend and its matching frontend together. Back up the database and keep `BOOKING_ENCRYPTION_KEY` and `DJANGO_SECRET_KEY` unchanged. In `/var/www/MyselfPay_Backend`, after the approved changes are on the deployed branch:

```bash
git status --short
git pull --ff-only origin main
./venv/bin/python -m pip install -r requirements.lock
./venv/bin/python manage.py migrate --noinput
./venv/bin/python manage.py collectstatic --noinput
./venv/bin/python manage.py check
sudo systemctl restart myselfpay-backend.service
sudo systemctl status myselfpay-backend.service --no-pager
```

Stop if Git reports local source changes or a failed command; do not overwrite server modifications or environment files. Keep using the existing service account/permissions.

Twilio Verify credentials must be configured in the admin panel or existing environment settings. SMTP credentials and `BOOKING_EMAIL_ENABLED=true` (or the saved SMTP setting) are still required for center notifications. Disabling Secure Mail alone does not enable SMTP delivery.

## Verification

Unit/API tests simulate Twilio and SMTP; they never send real codes or emails. Browser fixtures can enable `E2E_FAKE_SMS=true` only in their isolated DEBUG process. This flag has no implementation in the production application. Before rollout, verify an actual SMS on the configured Twilio account and a center notification on the deployed server.
