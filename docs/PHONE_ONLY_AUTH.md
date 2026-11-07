# Patient mobile OTP authentication

Registration: first name + country code/mobile -> SMS OTP -> account created and signed in.
Login: registered country code/mobile -> SMS OTP -> signed in.
No patient username, password, ZIP or email is requested. Existing accounts keep their member IDs and request history. A unique matching legacy saved phone is linked only after successful OTP verification; ambiguous or disabled accounts are rejected.

POST /api/auth/mobile/code accepts intent (register/login), phone, and name for registration. POST /api/auth/mobile/verify accepts the same intent/phone/name plus challenge, verification_id and six-digit code. Successful verification creates an HttpOnly session and CSRF token directly. Proofs are intent/phone/challenge-bound, expire and are consumed once. No proof token is returned to the client. Existing Origin/CSRF checks and Twilio Verify configuration remain active. No new environment values or schema migration are required.

Deploy the backend before the frontend. Retained username/password and reset endpoints support older clients; the current UI exposes only mobile OTP authentication. Booking, center OTP, Secure Mail routing, dashboards and admin login are unchanged. No live SMS is sent by tests.
