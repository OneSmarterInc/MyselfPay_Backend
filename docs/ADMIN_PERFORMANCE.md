# Admin performance improvements

The registered-user list previously issued one list query plus three display-column queries per row. A 50-row list therefore used 151 queries before pagination, session, permission and shared navigation work. Its annotated queryset now resolves usernames, names and suspension flags in one indexed SQL query. The regression test renders 50 rows and asserts exactly one query for these fields.

Admin TOTP permission is checked once per request; no authorization result is cached between requests, so a server-side reset takes effect on the next request. Pending price/profile navigation counts are cached for 15 seconds per database, reducing repeated connection checkout and count queries. They may lag changes by up to 15 seconds. Query failures are not cached. Mailer totals reuse the paginator count rather than executing the same filtered count again.

Audit logging, TOTP, CSRF checks, user permissions and email delivery remain enabled. No schema or frontend changes are required for this performance update. This was verified with synthetic local data; production network/SMTP/audit-store latency has not been measured.

Deploy backend main and restart the service. If the preceding TOTP release has not been deployed, also install requirements, apply migrations and collect static files as documented in ADMIN_TOTP.md.
