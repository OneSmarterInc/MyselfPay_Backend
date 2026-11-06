#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  echo 'Run with sudo bash ops/install-admin-mailer.sh' >&2
  exit 1
fi
cd /var/www/MyselfPay_Backend
test -f .env
test -x venv/bin/python
systemctl cat myselfpay-backend.service >/dev/null
mailer_user="$(systemctl show myselfpay-backend.service -p User --value)"
mailer_group="$(systemctl show myselfpay-backend.service -p Group --value)"
mailer_user="${mailer_user:-root}"
if ! [[ "$mailer_user" =~ ^[a-zA-Z0-9_-]+$ ]] || ! [[ "$mailer_group" =~ ^[a-zA-Z0-9_-]*$ ]]; then
  echo 'Configure the mailer unit manually for this backend service identity.' >&2
  exit 1
fi
cat > /etc/systemd/system/myselfpay-mailer.service <<UNIT
[Unit]
Description=MySelfPay admin mailer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$mailer_user
Group=$mailer_group
WorkingDirectory=/var/www/MyselfPay_Backend
EnvironmentFile=/var/www/MyselfPay_Backend/.env
ExecStart=/var/www/MyselfPay_Backend/venv/bin/python manage.py process_admin_mailer --watch --sync-contacts
Restart=on-failure
RestartSec=5
TimeoutStopSec=45
UMask=0077

[Install]
WantedBy=multi-user.target
UNIT
chmod 644 /etc/systemd/system/myselfpay-mailer.service
systemctl daemon-reload
systemctl enable myselfpay-mailer.service
systemctl restart myselfpay-mailer.service
systemctl status myselfpay-mailer.service --no-pager
