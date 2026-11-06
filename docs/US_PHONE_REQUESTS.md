# Request a Time with US phone verification

The Request a Time button opens an inline US phone and six-digit SMS OTP flow.
There is no registration/login choice and no required patient name, email,
username or password. A successful verification resumes the selected center.
Preferred slots and consent are still part of the appointment request.

`POST /api/auth/mobile/code` and `/verify` support `intent: "request"` alongside
the existing registration/login intents. The request intent accepts formatted US
national or +1 numbers and normalizes to E.164. `phonenumberslite` checks the US
numbering region, rejecting Canada and other non-US NANP numbers even though
they share +1. Numbering-plan validity does not prove activity or SMS reception;
successful Twilio OTP verification is required before continuing.

After OTP approval an existing mobile identity is reused, or a minimal internal
identity is created for request history. Unique legacy-phone accounts retain
their member ID. No email or password is collected. Existing saved US verification
can be reused; non-US identities cannot bypass the server-side request check.
The account profile's `booking_eligible` flag controls the frontend gate. Request
OTP proofs remain purpose-bound, expiring and single-use. Names on requests are
optional; absent names are not invented.

Deploy the backend **before** the matching frontend. Install the updated pinned
dependencies (`pip install -r requirements.lock`), apply existing migrations,
and restart the backend. No new migration is introduced for this flow. Center
delivery, Secure Mail approval and the admin mailer remain separate and unchanged.
