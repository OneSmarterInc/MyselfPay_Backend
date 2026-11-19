# Time requests for all directory centers

An approved scheduling email remains the first recipient choice. Centers without
an approved, valid scheduling email route to the operator-confirmed mailbox
`patientcare@onesmarterhealthweb.com`. Client input cannot change either recipient.
Billing, privacy and other non-scheduling contacts are not used for appointments.

Support receives the named center, preferred slots and existing verified approval
link. Support must contact the center and confirm availability before approving.
The patient-facing options and receipt identify this as a support notification.
Existing account verification, consent, idempotency and delivery controls apply.

With `SECURE_MAIL_ENABLED=true`, support notifications use the same center-only
Secure Mail workflow and durable worker as center notifications. No direct SMTP
fallback occurs on Secure Mail failure. With direct email delivery enabled, the
support mailbox must be included if an SMTP recipient allowlist is configured.
No database migration, dependency or new environment variable is required.

Deploy the updated backend on `codex/mobile-username-accounts` and restart
`myselfpay-backend.service`. Restart the existing Secure Mail handoff worker if it
runs in a separate process so it loads the updated support notification wording.
