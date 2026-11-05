# MySelfPay email settings

Apply Django migrations before starting the updated backend. Migration `0012_smtp_configuration` adds encrypted, administrator-managed SMTP settings. Existing environment settings remain in use until an administrator enables the saved profile.

1. Sign in to `/admin/` with a Django superuser.
2. Open **Email / SMTP settings**, then add the single configuration record.
3. Enter the host, port, username, SMTP password/app password, sender email and sender name.
4. Choose STARTTLS (usually 587) or implicit TLS (usually 465). Plaintext SMTP is not supported.
5. Enable **Use saved SMTP settings**. Enable direct booking emails if MySelfPay should send center request notifications. Email verification also uses this SMTP connection.
6. For production center delivery, leave **Allowed recipients** blank, or include every intended center mailbox. A restrictive testing list can exclude a center even when another center receives mail.
7. Save. The next email uses the updated profile without restarting workers. Leave the password blank when editing to retain it. Turn off **Use saved SMTP settings** to restore the environment profile; profiles are never mixed.

Passwords are encrypted in the database and never rendered back in forms. Keep the Django secret key stable and back it up securely: changing it prevents decryption of saved credentials. No secrets belong in source control.

To explicitly send a configuration test, save a test recipient, then run:

```bash
./venv/bin/python manage.py check_smtp --send
```

SMTP acceptance does not confirm inbox delivery. Check the mailbox's spam folder and the sending provider's bounce/delivery logs.

## A missing center notification

Open the request in **Appointment requests** to see its recipient, route, state and delivery guidance. For the reported hosted request, run this read-only command on the AWS backend:

```bash
./venv/bin/python manage.py diagnose_booking_email MSP-0618E4416CC88C69
```

It sends nothing and displays no passwords, OTPs, patient email addresses, or request-link tokens. It shows the saved center recipient, current published center recipient, delivery route, queue state and attempts. Historical requests retain the recipient saved at submission; changing a directory email does not reroute them automatically. No request is automatically resent by this command.

If **Secure Mail** is the route, its service sends the invitations using its own configuration. Changing MySelfPay SMTP does not change the Secure Mail service. Check `process_booking_chats --watch`, the backend's `secure_mail_handoff` warnings, and Secure Mail's outbox/delivery logs. If the patient and center share an email, test with separate inboxes; duplicate participant handling belongs to Secure Mail and cannot be diagnosed from SMTP acceptance alone.
