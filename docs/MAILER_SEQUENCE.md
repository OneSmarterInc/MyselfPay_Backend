# Backend mailer sequence

The directory is sorted in Django: centers with at least one SMTP-accepted sent
message first, then newest imported centers, followed by stable center/email/ID
tie-breakers. The same query is used by pagination and campaign count/range
selection. Each response contains at most 50 center rows; the browser no longer
downloads all pages to sort them.

Sent emails and Replies received are database filters. Replies received includes
replies on older messages, even when the most recent message has no reply. Each
row shows its latest delivery state separately from the reply count and links to
the most recently received reply's thread. Shared addresses retain one row per
center. Uncertain messages are not relabeled as sent and are never replayed.

Deploy the frontend main update to remove the former Vercel HTML adapter, then
deploy this backend branch. No schema migration was added:

```bash
cd /var/www/MyselfPay_Backend
sudo git fetch origin
sudo git switch codex/mobile-username-accounts
sudo git pull --ff-only origin codex/mobile-username-accounts
sudo ./venv/bin/python manage.py collectstatic --noinput
sudo ./venv/bin/python manage.py check
sudo systemctl restart myselfpay-backend.service myselfpay-mailer.service
```

If git reports local modifications, preserve them before pulling. Do not reset
or overwrite local configuration. This release changes neither SMTP credentials
nor the worker's existing delivery states.

SMTP failures are now classified: connection, TLS, authentication and explicit
recipient/message rejection are Failed with a safe stage/code. A missing
acknowledgement during submission stays Uncertain. Old uncertain records are
unchanged because their original transport error was not stored. No retries
are triggered. Run the admin SMTP test after deployment to identify live
provider/configuration failures; code cannot repair those credentials itself.

Validation: 33 mailer tests passed with managed models and admin-only URL
configuration (the unrelated H3 native dependency is blocked by Windows policy).
Three transport tests passed with mocked SMTP. Frontend build and 65 unit tests
passed; rendered synthetic admin HTML was checked at 390px and 1440px.
