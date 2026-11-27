# Center email OTP sign-in

The center sign-in page now requests a six-digit emailed code using POST /api/center-pro/auth/otp/request and verifies it with POST /api/center-pro/auth/otp/verify. A valid code opens the workspace directly. Owner/staff roles come only from active memberships, never submitted role fields. Exact directory contact emails retain their existing default-owner provisioning behavior.

Codes expire after 10 minutes, are single-use, are stored as salted password hashes with hashed challenge IDs, and lock after five incorrect attempts. Resending invalidates earlier unconsumed codes. Email requests and verification attempts are rate-limited. Both endpoints enforce CSRF and the existing trusted-origin policy. Unknown/unassigned emails get the same generic request response; no production code is returned to the browser. Verification rechecks active account and active center membership. Administrator MFA flags are cleared on center login.

Email delivery uses the existing booking_mail / Secure Mail transport. No new environment variables are required. DEBUG plus CENTER_PRO_LOCAL_LINKS enables development-only inline codes, equivalent to the existing local-link debug option; it is never enabled by that flag alone in production. Mail failures are not reported as successful delivery. Delivery-uncertain responses retain the code-entry step so a delivered code can still be used.

Migration 0011_center_login_otp adds the CenterLoginOTP table to the existing application database; it does not alter CHR tables or memberships.

```bash
cd /var/www/MyselfPay_Backend
source venv/bin/activate
git pull --ff-only origin main &&
python manage.py migrate --noinput &&
python manage.py check &&
sudo systemctl restart myselfpay-backend.service &&
sudo systemctl status myselfpay-backend.service --no-pager
```

Deploy frontend main on Vercel after the backend update. Automatic Git deployment is sufficient; redeploy manually if automatic deployment is disabled or fails. The normal login UI no longer offers a sign-in link. Existing link verification endpoints remain compatible with already-issued links during their original short expiry period.
