

## Saved email verification (2026-11-03)
Run `./venv/bin/python manage.py migrate --noinput` before restarting the backend.
Successful email OTP verification is retained in `booking_verified_emails`, bound to the authenticated account ID hash and normalized email hash. Email addresses remain encrypted with BOOKING_ENCRYPTION_KEY. Existing successful checks are migrated where still available. OTP codes and reusable proof tokens are not stored in this table.
Booking options return only the current account's latest verified email. The UI skips the OTP step for that address. Creating a booking with a saved email checks the database again; a different address or another account must verify separately. Logging out does not remove the saved verification; authentication is still mandatory. SMS verification remains optional.
