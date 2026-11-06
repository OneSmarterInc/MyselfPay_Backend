# Center-confirmed appointment time

Approval now requires an `appointment` object on both `/api/booking/status`
(verified clinic-link session) and `/api/center/request` (verified center session).
Select a requested window with `{"preferred_index":0}` (zero-based index), or
propose an exact local time with `{"date":"2026-11-12","time":"14:30"}`.

The server uses the original request's clinic timezone; clients cannot replace it.
Both paths enforce ownership, unexpired verification, a future date within 90
days, and valid local clock time. Nonexistent or ambiguous daylight-saving times
are rejected. Approval and the encrypted `approved_slot` payload are saved in
the same locked transaction. Identical retries are idempotent; another time
cannot replace an already-approved slot. Follow-up and decline need no slot.

`approved_slot` is returned in receipts, user dashboards and center dashboards.
The admin request detail shows the confirmed appointment. On the frontend,
approved requests show this time prominently, with alternative-time wording when
applicable. Waiting instructions and pending progress steps are hidden after
approval. Original preferred times remain in request details for reference.
Historical approvals without a saved slot are labeled as unrecorded, never
assigned an invented appointment time.

No migration is required: the snapshot uses the existing encrypted request
payload. Deploy the updated backend and matching frontend together; older
clients that approve without a selection receive a validation error.
