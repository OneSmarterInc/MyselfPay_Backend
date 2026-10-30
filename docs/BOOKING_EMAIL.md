# Booking email and portal test center

This release adds a labeled **Geetesh Pawar Test Imaging Center** in Dayton, Ohio with MRI and CT **test scheduling** services. It is fictional and provides no medical care. Its prices are quote-only; no clinical prices or appointment availability are invented. The center is created by an operator command, not by application startup.

## Server environment

Keep your existing `DATABASE_URL`, `DJANGO_SECRET_KEY`, `FRONTEND_ORIGIN` and **stable** `BOOKING_ENCRYPTION_KEY`. Add or update the following in the AWS backend's `.env`. SMTP credentials belong only on the server; never put them in Vercel `VITE_` variables or commit them.

```dotenv
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USE_TLS=true
SMTP_USE_SSL=false
SMTP_USERNAME=your-sending-account@gmail.com
SMTP_PASSWORD='your-smtp-app-password'
SMTP_FROM=your-sending-account@gmail.com
SMTP_FROM_NAME=MySelfPay
BOOKING_EMAIL_ENABLED=true
BOOKING_EMAIL_ALLOWED_RECIPIENTS=your-confirmed-test-center-recipient@example.com
TEST_CENTER_EMAIL=your-confirmed-test-center-recipient@example.com
```

`SMTP_USERNAME`, `SMTP_PASSWORD` and `SMTP_FROM` are your **sender** account credentials, not necessarily the center recipient's account. The inbox name is displayed as MySelfPay. Gmail requires its supported app-password setup; another SMTP provider can be used with its verified sender and credentials. For implicit TLS on port 465, set `SMTP_USE_SSL=true` and `SMTP_USE_TLS=false`. Plaintext SMTP is not supported. Provider sender authorization still applies.

The recipient allowlist limits center notifications to your test mailbox during rollout. Patient verification codes still go to the patient's entered address. Only remove the allowlist when you intend to enable all approved scheduling recipients in the catalog; API callers cannot override a center recipient.

## AWS activation

The code cannot configure your private AWS `.env` or restart EC2 from GitHub. On your server, use the same account/environment that currently runs the backend. Your earlier `.env` was readable as root, so if needed run `sudo -i` **first**, then `cd /var/www/MyselfPay_Backend`. Do not overwrite your locally modified `core.py` or existing directory import files.

1. Take and verify a backup of `newselfpaydb` before the additive migration and directory publication. Keep it in a restricted backup location. Preserve the existing change-record process for any database to which that policy applies.
2. Pull the new code with `git pull --ff-only origin main`. This release does not modify `core.py`; your imported directory is not replaced by a demo seed.
3. Edit `.env` with your sender credentials, recipient and email enable switch. Preserve its existing working ownership/access for the service account.
4. Run these from `/var/www/MyselfPay_Backend`:

```bash
set -a
source .env
set +a
./venv/bin/python manage.py migrate --noinput
./venv/bin/python manage.py check_smtp
./venv/bin/python manage.py check_smtp --send
./venv/bin/python manage.py add_test_center
./venv/bin/python manage.py add_test_center --publish
sudo systemctl restart myselfpay-backend.service
```

`check_smtp --send` sends one explicitly requested configuration email to `TEST_CENTER_EMAIL`. Success means the SMTP server accepted it; check inbox/spam as well. The center command first previews, then `--publish` clones the active directory transactionally and adds only the test center plus its two test-service offers. Previous batches and their data remain available. With 3,463 existing listings, the published directory becomes **3,464**. Repeating the command with unchanged settings does not duplicate it.

5. Search **Dayton, OH** or open **Directory** and filter by **Geetesh**. Submit an appointment with synthetic patient details and an inbox you control. Verify the patient email, submit once, check the center inbox, open its secure link, request the center OTP, then record Scheduled / Left voicemail / Can't accommodate. Confirm the patient tracking page updates.

## Delivery and retries

New booking records and outbox entries commit before SMTP is attempted. A failed send does not lose the booking or consume a second appointment submission. API receipts show `sent`, `queued`, `sending`, `failed`, `uncertain` or legacy `suppressed` truthfully. Old suppressed requests are never automatically emailed when the switch is enabled.

The additive `0002_booking_outbox` migration creates a separate table. The legacy `booking_requests.delivery` column remains compatible with its original constraint; API delivery status is read from the new outbox. Back up and migrate before restarting the new backend.

Run `python manage.py process_booking_emails` to inspect pending counts. Schedule **`python manage.py process_booking_emails --send`** every minute using your backend's existing service account and environment to retry queued or safely failed mail (maximum five attempts). SMTP calls have a 15-second network timeout. Atomic claims prevent simultaneous workers from sending the same queue item. If a connection breaks after message submission or a worker dies mid-send, the item becomes uncertain and is not automatically resent; check the inbox/provider before any manual resend. SMTP cannot guarantee exactly-once inbox delivery.

No request bodies, patient contacts, passwords, OTPs or capability links are logged by this mail workflow. Queue rows contain request IDs and delivery metadata; request details remain encrypted. Clinic notification emails keep patient contact details behind OTP. Authorized clinic views include verified patient email and phone. Mail contains plain-text and HTML alternatives.

## Booking email text

```text
From: MySelfPay <YOUR_SMTP_FROM>
To: CENTER_REGISTERED_EMAIL
Subject: New self-pay request BOOKING_ID

New self-pay request BOOKING_ID
Clinic: Geetesh Pawar Test Imaging Center
Exam: MRI Knee
Contrast: Without contrast
Preferred times: YYYY-MM-DD Morning (8 am–12 pm) (Eastern time)
Price: Clinic will confirm the exact total.
TEST CENTER: This is a synthetic booking workflow; no medical care is provided.
Open the secure link to review patient contact details.
https://myselfpay-frontend.vercel.app/#clinic/BOOKING_ID/SECURE_TOKEN

This link expires in 72 hours. A one-time code is required.
This is a request, not a confirmed appointment.

Please do not reply to this automated email.
```

Official setup references: [Python SMTP TLS transport](https://docs.python.org/3/library/smtplib.html), [Google app passwords](https://support.google.com/accounts/answer/185833).
