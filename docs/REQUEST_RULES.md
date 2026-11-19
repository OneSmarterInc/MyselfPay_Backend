# Request rules, Forget me and retention

## Rules the code enforces

- A request stays open for **7 days** (`booking.REQUEST_LIFETIME`), then closes as `expired`.
- A member has at most **3 open requests** and **one per facility and exam** (same service and exam text). The 4th and a repeat return HTTP 409 with a plain message.
- When a facility confirms a time, the member's other open requests for the same exam close as `cancelled` with reason `scheduled_elsewhere`. A different exam stays open.
- `POST /api/booking/cancel {"id": ...}` (signed-in member) closes a request as `cancelled`, reason `patient_cancelled`. A confirmed time can be cancelled too.
- Facilities on the email route get a short notice (no patient details) when a request closes because the patient cancelled or scheduled elsewhere. Facilities on other routes see the `cancelled` status in their portal.
- The facility's "Can't schedule" outcome accepts an optional `reason`: `no_openings`, `not_offered` or `other`. It is returned as `closed_reason` on the receipt.
- The center login answers every email the same way (`verification_id` and `expires_in`). An email that matches no listing gets no code and its `verification_id` never verifies.

## Forget me

`POST /api/account/forget` for a signed-in member.

1. With no `code`, it texts a 6-digit code to the number on the account and returns `verification_id`.
2. With `verification_id` and `code`, it cancels open requests, emails the facilities, deletes the member, the account, the phone records, requests and sessions (the same records as an admin deletion), and clears the session cookie.

## Retention job

`python manage.py purge_expired_data` deletes accounts 30 days after their last request closed, then request details 30 days after closing. Accounts with no requests are kept. **Schedule it daily** with the restricted database role (see docs/AWS.md). It is not scheduled by this change.

## Database change (manual, hosted PostgreSQL)

`ops/booking-status-cancelled.sql` widens the `status` CHECK to allow `cancelled`. Apply it before deploying this code. Change record: verified backup first, Vikram's approval, run once, verify with `SELECT conname FROM pg_constraint WHERE conrelid='booking_requests'::regclass`. No rows change. Local databases get it from `ops/booking-schema.sql`.

## Not in this change

Reminder email after 2 business days and the support list after 4, response-time and exam-completed records, hours, orders, the accept-requests switch and the appointment inbox field. These need new tables and a scheduler and come next.

## Request inbox

A center can choose where new patient requests are emailed. It enters an address, MySelfPay sends a test message with a 6-digit code to that address, and the center enters the code. Only then does the inbox take effect. The login email still owns every request, so the dashboard, history and audit trail do not change. The inbox applies to requests created after it is verified and only for facilities that already receive requests at their own listing email. Removing it sends new requests back to the listing email. Limits: 5 test messages per session per hour, 5 wrong codes per test, 10-minute code life. Endpoints: `POST /api/center/inbox`, `inbox-verify`, `inbox-remove`; the dashboard returns `request_inbox`.

Manual step: `ops/center-inboxes.sql` adds two tables (`center_inboxes`, `center_inbox_checks`). It only creates tables, so no rows change. Apply it before deploying this code. Change record: verified backup first, Vikram's approval, run once, verify with `SELECT to_regclass('center_inboxes')`.
