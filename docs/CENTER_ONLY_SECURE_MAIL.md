# Center-only Secure Mail booking delivery

Deploy the companion securemail-backend provider-only contract before this SelfPay update. No migration or patient frontend change is required.

Set SECURE_MAIL_ENABLED=true and retain SECURE_MAIL_URL, SECURE_MAIL_PORTAL_URL, SECURE_MAIL_KEY_ID, SECURE_MAIL_SECRET and existing mTLS configuration. Keep the process_booking_chats --watch worker supervised. In Secure Mail, the selfpay integration must allow the MySelfPay frontend in provider_action_origins and set suppress_provider_notifications=false. Its existing SMTP/outbox and key service must be operational.

Only new requests are queued with delivery_route=center_secure_mail. The signed payload uses delivery_mode=provider_only and one provider mailbox from the approved catalog. No patient email, fabricated requester, patient invitation, patient confirmation email or patient chat link is used. The invitation contains a portal link, not the approval capability. Center mailbox OTP and the existing clinic approval verification stay enforced. Approval updates existing user/admin dashboards.

Retries use the same request ID. Historical mobile direct-email requests are not replayed into Secure Mail, and historical two-party handoffs/confirmation events remain disabled. Do not requeue old requests as part of deployment.
