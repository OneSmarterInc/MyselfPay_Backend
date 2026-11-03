# MySelfPay administration

Public URL: https://myselfpay.onesmarter.com/admin/ (Vercel proxies this path and admin assets to Django).

## Install on AWS

```bash
cd /var/www/MyselfPay_Backend
git pull --ff-only origin main
./venv/bin/python -m pip install -r requirements.txt
./venv/bin/python manage.py migrate --noinput
./venv/bin/python manage.py collectstatic --noinput
./venv/bin/python manage.py createsuperuser
sudo systemctl restart myselfpay-backend.service
```

Use the username and password created by createsuperuser. Only active Django superusers can access the dashboard; portal and guest sessions do not grant administration rights. Django administrator sessions use a different HttpOnly cookie from portal sessions. The superuser command prompts privately for a password; do not place credentials in scripts or source control.

Dashboard: registered account count, suspended count, and saved Twilio configuration status. Registered portal users can be searched, edited, suspended or restored. Password reset uses the existing portal scrypt format and revokes their sessions. Changing email updates both existing account tables; deletion and account creation are not exposed here. Django Users manages separate administrator credentials.

Twilio settings accept API Key SID (SK), API key secret, optional Account SID (AC), and Verify Service SID (VA). Blank secret retains the saved value. The clear checkbox removes the stored override. Secrets are encrypted before saving using a key derived from the stable DJANGO_SECRET_KEY and a dedicated purpose. Back up this key; changing it requires re-entering stored Twilio secrets. Stored values take precedence over environment values through admin_settings.twilio_configuration(); fallback names include TWILIO_API_KEY_SID / SID and TWILIO_API_KEY_SECRET / CLIENTSECERT. These settings prepare configuration; mobile OTP delivery is not yet implemented or activated by saving this form.

The deployment must forward /admin/ and /admin-assets/ to the Django service. Django serves collected assets from STATIC_ROOT. If your AWS reverse proxy only forwards /api/, add both paths using its existing Django upstream. Keep FRONTEND_ORIGIN set to the HTTPS public origin; admin POSTs use standard Django CSRF validation as well as the project's origin checks.

## MySelfPay interface

The admin uses MySelfPay login and dashboard templates, branded navigation and responsive styles from api/static/selfpay-admin. After updating the design, run collectstatic --noinput and restart the backend; existing superuser credentials remain valid.
