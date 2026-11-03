# User dashboard

GET /api/dashboard requires an authenticated, registered portal account. It returns that account's profile, latest verified booking email, status totals and ten appointment requests per page. Use filter=open for active requested/voicemail requests; default filter=all. Request ownership comes only from the authenticated member ID, never query parameters. Expired requests are shown as expired even before their tracking page is opened. Existing retention rules still apply.

The React #dashboard route offers Overview, My Profile, Open Requests and All Requests, with request links and automatic status refresh every 30 seconds. Profile details are read-only. Guest users are directed to log in.

Booking no longer asks for a time zone. Options supply a default from the center's explicit timezone/time_zone field where present, otherwise its state (or Eastern for unspecified/multiple states). The time zone used is displayed with the time picker; split-zone locations should set an explicit center timezone. Existing API clients may still submit a supported zone.

Deploy: pull main, apply any outstanding migrations, restart myselfpay-backend.service. This dashboard change adds no migration.
