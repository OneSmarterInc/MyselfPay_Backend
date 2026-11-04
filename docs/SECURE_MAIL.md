# Booking conversations before clinic approval

After patient email verification and request submission, SelfPay queues a Secure Mail handoff in the booking transaction. Direct clinic request emails are disabled while SECURE_MAIL_ENABLED=true, including retries of old direct-email jobs. Secure Mail sends its own generic invitations to both participants from support@onesmarter.com. The invitation never contains the clinic confirmation capability.

The encrypted thread metadata holds a provider_action URL and expiry. Only the verified provider participant can retrieve it. The clinic sees Review & confirm request inside Secure Mail, opens the existing SelfPay clinic workflow and verifies its email there before approving. The patient can chat before approval but cannot retrieve the clinic link. Existing completed threads are not replayed automatically.

Migrate before enabling SECURE_MAIL_ENABLED=true. Set SECURE_MAIL_URL, SECURE_MAIL_PORTAL_URL, SECURE_MAIL_KEY_ID and SECURE_MAIL_SECRET. In the matching Secure Mail integration set suppress_provider_notifications=false and provider_action_origins to the exact trusted SelfPay frontend origin, for example ["https://selfpay.example.com"]. Production requires HTTPS; loopback HTTP requires explicit development configuration. Configure mTLS using SECURE_MAIL_CA_FILE, SECURE_MAIL_CLIENT_CERT and SECURE_MAIL_CLIENT_KEY as applicable.

Run python manage.py process_booking_chats --watch as a supervised worker, plus Secure Mail's delivery worker. Retries reuse the booking ID and deduplicate chat creation and invitations. No new migration is required beyond 0010_booking_chat. Follow the repository policy for live database operations.
