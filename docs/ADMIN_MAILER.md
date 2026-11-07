# Admin center mailer

Open `/admin/mailer/` using a Django superuser. Refresh directory emails to load
valid addresses from the active published directory. Patient account emails are
never included. Each directory center has its own row even when centers share an
email. The date is the center's earliest recorded published batch date; the legacy
schema cannot establish the exact date an email was originally added. Rows sort
newest first, then by center key, email and ID for stable ties.

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


## One center per row and checkbox selection

Directory recipients now use the directory entry key plus email, not email alone.
Centers sharing a mailbox remain separate rows and receive separate personalized
messages if both are selected. All prior campaigns, messages, and replies remain
available. Migration 0016 retires grouped contacts; run `python manage.py
sync_mailer_contacts` after migration (or Refresh directory emails in the admin)
to populate the current center rows. No emails are sent by refreshing contacts.

Select individual checkboxes or Select all centers on this page. Checkboxes apply
to the current page and override an entered count/range. The preview lists exactly
which center rows will receive mail. Preview never sends; Confirm and queue does.
Each message keeps its own Message-ID so replies attach to the corresponding
center email thread, even when recipients share an address.

Deploy on AWS:

```bash
cd /var/www/MyselfPay_Backend
sudo systemctl stop myselfpay-mailer.service
sudo git pull --ff-only origin codex/mobile-username-accounts
sudo ./venv/bin/python manage.py migrate --noinput
sudo ./venv/bin/python manage.py sync_mailer_contacts
sudo ./venv/bin/python manage.py collectstatic --noinput
sudo ./venv/bin/python manage.py check
sudo systemctl restart myselfpay-backend.service
sudo systemctl start myselfpay-mailer.service
```

If the optional mailer service is not installed, omit its stop/start lines.


Email search accepts a partial address or domain, case-insensitively. Results stay
newest center import first; pagination retains the filter. Count/range previews
use that same filtered order. Selected checkboxes must belong to the filtered
results. Clear removes the filter. This update requires no new migration.


Mailer campaigns are sent as multipart/alternative: a branded, inline-styled HTML
email plus a plain-text fallback. Subject and message are personalized and escaped;
the composer accepts plain text, not arbitrary HTML. The confirmation screen shows
the same HTML card as delivery. The footer introduces MySelfPay, links to the
portal, and uses the saved campaign Reply-To. Previously sent emails are unchanged.


## Outreach variables and recipient preferences

The default composer contains the Vikram Sethi outreach structure. Variables are
filled per center: `{{facility_name}}` (also `{{center_name}}`), `{{city}}`,
`{{state}}`, `{{email}}`, `{{claim_link}}`, `{{mailing_address}}`, and
`{{opt_out_link}}`. Unknown variable names are rejected. The directory supplies
city/area and state; missing values are not invented. These values are frozen in
the campaign preview. The postal address is editable in Reply mailbox settings;
it defaults to the operator-provided 707 Miamisburg-Centerville Road, STE 223,
Dayton, OH 45459. An email address is not accepted as a postal address.

The center link opens the existing center portal, which requires email OTP.
It does not bypass verification or introduce listing-edit permissions. The draft
says to reply for listing corrections because direct editing is not implemented.

History filters operate by recipient email across centers sharing a mailbox:
- Never emailed: no sent, uncertain, or currently-sending message recorded.
- Emailed, no reply: at least one SMTP-accepted message and no imported reply.
  Use Check for replies to import the latest IMAP replies first.
- Unsubscribed / disabled: addresses blocked from sending.

The unsubscribe list is at `/admin/mailer/unsubscribed/` and includes inactive
contacts too. Disable email is a superuser-only CSRF-protected POST. It adds the
address to the same email-level suppression list as recipient unsubscribe.
Queued and draft messages are suppressed, and sending checks the suppression
list again just before SMTP. A message already in transit cannot be recalled.
Directory refresh does not remove suppression records. No automatic re-enable
or automated resends are provided.

Recipient links are signed with a dedicated Django signing salt. GET displays a
confirmation; only CSRF-protected POST changes preferences. This avoids accidental
unsubscribes from link scanners. A List-Unsubscribe header points to the same
confirmation page; this does not advertise RFC 8058 one-click unsubscribe.
The preference affects mailer campaigns only, not appointment transactions.

Deploy this version on AWS (no Vercel change):

```bash
cd /var/www/MyselfPay_Backend
sudo systemctl stop myselfpay-mailer.service
sudo git pull --ff-only origin codex/mobile-username-accounts
sudo ./venv/bin/python manage.py migrate --noinput
sudo ./venv/bin/python manage.py sync_mailer_contacts
sudo ./venv/bin/python manage.py collectstatic --noinput
sudo ./venv/bin/python manage.py check
sudo systemctl restart myselfpay-backend.service
sudo systemctl start myselfpay-mailer.service
```
