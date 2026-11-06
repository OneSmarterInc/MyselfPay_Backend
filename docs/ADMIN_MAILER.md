# Admin center mailer

Open `/admin/mailer/` using a Django superuser. Refresh directory emails to load
valid addresses from the active published directory. Patient account emails are
never included. Shared addresses are deduplicated and all associated center names
are shown. The date is the center's earliest recorded published batch date; the
legacy schema cannot establish the exact date an email was originally added.
For shared emails the most recently imported associated center determines order.
Rows sort descending by this date, then alphabetically by email for stable ties.

Enter `100` for positions 1–100 or `100-200` for positions 100–200 inclusive (101
addresses). Select a saved template or enter subject/message; `{{center_name}}`
and `{{email}}` are substituted per recipient. Preview freezes recipient order,
addresses, message and Reply-To. No email is sent until the admin confirms that
campaign. Confirmation is idempotent. New imports do not change a draft's saved
recipients. Cancel stops only unsent recipients, not messages already in flight.

The existing admin SMTP settings supply the transport. SMTP testing allowlists
still apply. Configure the real sender and mailbox before sending a campaign.
Messages are individual, with unique Message-ID and Reply-To set to
`patientcare@onesmarterhealthweb.com` by default. The composer does not call SMTP
inside an HTTP request. SMTP acceptance is displayed separately from actual
inbox delivery; transport failures are marked uncertain and not auto-replayed.

## IONOS reply mailbox

Save `imap.ionos.com`, port `993`, username and Reply-To
`patientcare@onesmarterhealthweb.com`, folder `INBOX`, and the mailbox password in
the admin form. Enable it after saving valid credentials. Passwords and imported
reply bodies are encrypted with purpose-specific keys derived from
`DJANGO_SECRET_KEY`; keep that key stable and backed up.

The worker checks replies every minute. “Check for replies” requests a background
sync; it does not make the web request wait on IMAP. IMAP uses TLS validation,
read-only folder selection and BODY.PEEK, without deleting or marking mail read.
Only replies referencing an outbound Message-ID and matching its recipient address
are stored, never unrelated inbox contents. Attachments are not imported. Full
messages over 1 MiB are skipped, and only 50,000 characters of a reply are stored.
Replies without matching References/In-Reply-To, or sent from another address,
need to be checked in the mailbox. Matching is threading, not proof of sender
authenticity; it never approves a booking or triggers an automatic reply.

## AWS deployment

Pull `codex/mobile-username-accounts`, then:

```bash
cd /var/www/MyselfPay_Backend
sudo ./venv/bin/python manage.py migrate --noinput
sudo ./venv/bin/python manage.py collectstatic --noinput
sudo ./venv/bin/python manage.py check
sudo systemctl restart myselfpay-backend.service
sudo bash ops/install-admin-mailer.sh
```

The installer copies the service identity of `myselfpay-backend.service`, writes
only `/etc/systemd/system/myselfpay-mailer.service`, and starts that worker.
For a manual installation, use the unit below and match its User/Group to your
existing backend service.

Install a separate worker service using the server's existing backend environment
file and Python environment. Do not run duplicate workers or use the development
SMTP/null transport for a real campaign.

```ini
[Unit]
Description=MySelfPay admin mailer
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/var/www/MyselfPay_Backend
EnvironmentFile=/var/www/MyselfPay_Backend/.env
ExecStart=/var/www/MyselfPay_Backend/venv/bin/python manage.py process_admin_mailer --watch --sync-contacts
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Save as `/etc/systemd/system/myselfpay-mailer.service`, then run:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now myselfpay-mailer.service
sudo systemctl status myselfpay-mailer.service --no-pager
```

The service user needs read access to the existing environment file. No secrets
are embedded in the unit, campaign pages or command output. This worker can send
already-confirmed campaigns; tests use mocked SMTP and IMAP only.
