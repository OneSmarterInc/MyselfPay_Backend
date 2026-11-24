# Secure Mail public API transport

Use the supplied OneSmarter public API v1 with a server-side Bearer key, created with the SelfPay profile on the configured SelfPay owner. Enable status:read, messages:write, bookings:write and bookings:read. The sender must be an address authorized on that key owner. Do not commit or share the key.

Update the existing backend environment file (not the frontend):

```dotenv
SECURE_MAIL_ENABLED=true
SECURE_MAIL_TRANSPORT=api
SECUREMAIL_BASE_URL=https://securemailbackend.onesmarter.com/api/v1
SECUREMAIL_API_KEY=REPLACE_WITH_PRIVATE_API_KEY
SECUREMAIL_MESSAGES_ENABLED=true
SECUREMAIL_SENDER=REPLACE_WITH_AUTHORIZED_SENDER_EMAIL
```

`SECURE_MAIL_ENABLED` routes new clinic appointment notifications through the existing durable process_booking_chats worker. `SECURE_MAIL_TRANSPORT=api` selects /bookings instead of the HMAC endpoint for each handoff; it never invokes both. Existing failed HMAC jobs should be inspected before switching because the API and HMAC namespaces are separate. Successfully handed-off jobs are not resent.

`SECUREMAIL_MESSAGES_ENABLED` routes booking_mail transactional messages, including center sign-in links, through /messages. Notifications lead to protected Secure Mail content; this is not an ordinary HTML SMTP relay. SelfPay-profile templates cannot be replaced. Outreach mailer SMTP is a separate transport and is not changed by this switch. No attachments are sent by this adapter. HTTP acceptance does not confirm inbox delivery. Inspect delivery outcomes in Secure Mail.

For API mode the HMAC secret and client certificate variables are not used by the public client. Retain old values privately if rollback is needed. No VITE_ key is allowed. API requests verify HTTPS certificates and reject redirects. POST retries retain stable content-derived idempotency keys; failures never fall back to SMTP.

After pulling main and editing the backend environment, restart myselfpay-backend.service and the existing supervised process_booking_chats worker. Run `python manage.py check_securemail_api` in the configured backend environment for a read-only readiness check. No migration or Vercel deployment is needed.
