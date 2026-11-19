# Responsive admin workspace

The shared workspace stylesheet and script preserve MySelfPay branding and the
existing Django admin functionality. Desktop uses a fixed, independently scrolling
sidebar and sticky header. Below 961px, navigation is an explicit Menu button with
aria-expanded and Escape-to-close support. Forms stack on small screens; data
tables scroll inside their panels instead of expanding the page.

Validated dashboard, registered users, SMTP settings, mailer, appointment list,
and login at 390, 820 and 1440px with synthetic data. SMTP and IMAP were not contacted.

Deploy from codex/mobile-username-accounts on AWS:

```bash
cd /var/www/MyselfPay_Backend
sudo git pull --ff-only origin codex/mobile-username-accounts
sudo ./venv/bin/python manage.py collectstatic --noinput
sudo ./venv/bin/python manage.py check
sudo systemctl restart myselfpay-backend.service
```

No schema change is needed for this visual update. Refresh the admin page after
static collection. If earlier mailer changes have not been deployed, run their
migration and contact synchronization steps first.
