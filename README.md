# MySelfPay backend — Django + PostgreSQL

Django 5.2 LTS API for the separately hosted [React frontend](https://github.com/OneSmarterInc/Myselfpay_Frontend). Migrated from SelfPay main commit `bf839ef9af6efde48b2666cc26e7e793a7d9bd06`.

The existing parameterized SQL domain services are intentionally retained so search qualifications, tenant isolation, session rotation, guest quotas, encrypted booking details and one-use email verification retain their behavior. Django owns HTTP routing, configuration, security middleware, migrations, management commands and WSGI serving. This is not a Django ORM rewrite. PostgreSQL is required in production; SQLite is an optional development/test fallback.

## Run locally

Requires Python 3.12+ and PostgreSQL 17+ (or Docker for the local database).

```sh
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.lock
cp .env.example .env
```

Set `DATABASE_URL` to a **new, disposable local database**. For Docker, set `POSTGRES_PASSWORD` in `.env`, run `docker compose up -d db`, and use that password in `DATABASE_URL`. Generate `BOOKING_ENCRYPTION_KEY` with the command in `.env.example`; keep this key stable across releases. Set a random `DJANGO_SECRET_KEY`.

```sh
python manage.py migrate
python manage.py seed_demo
python manage.py runserver 127.0.0.1:8000
```

Start the frontend at `http://localhost:5173`. Use the frontend's Vite proxy; keep `FRONTEND_ORIGIN=http://localhost:5173` exactly. `/api/health` returns 200 after the schema is ready. Seeding installs the bundled **74 research entries / 57 offers**, not the current production directory. The seeder refuses to replace an active directory or run with production settings. No demo accounts or passwords are installed.

For SQLite-only development, remove `DATABASE_URL` from `.env` while retaining `DJANGO_DEBUG=true`. It is never used as a production fallback.

## API

All paths are under `/api`. Requests with a body use JSON. Cookies are HttpOnly; tokens are never stored in browser local storage. Every mutation requires the exact configured `Origin`. Authenticated mutations additionally require the `X-CSRF-Token` returned by `/guest`, `/register`, `/login` or `/me`. The API preserves SelfPay's existing session CSRF protocol instead of Django's form-cookie token; its dispatch view is explicitly exempt from the form CSRF middleware and checked by the API boundary. CORS permits only `FRONTEND_ORIGIN` with credentials.

| Endpoints | Methods / use |
| --- | --- |
| `/health`, `/directory-summary` | GET, public readiness and counts |
| `/guest`, `/register`, `/login`, `/logout` | POST, session lifecycle |
| `/me`, `/catalog`, `/guest-usage` | GET, authenticated |
| `/search`, `/agent` | POST, directory search / deterministic guided search |
| `/conversations` | GET / POST, account-owned saved searches and history |
| `/conversations/{id}` | DELETE, owner only |
| `/preferences` | GET / POST / DELETE, account location preference |
| `/corrections` | POST, account listing correction |
| `/booking/options`, `/booking/email-code`, `/booking/email-verify`, `/booking/create` | POST, authenticated patient request flow |
| `/booking/patient` | POST, patient tracking capability |
| `/booking/code`, `/booking/verify`, `/booking/details`, `/booking/status`, `/booking/lock` | POST, clinic capability flow |

Guests have three chat conversations, five messages each, 500 characters per message. Guest chat text is not saved. Accounts support saved history. Password reset, payment processing, insurance calculations, external AI and speech are not implemented in the source and are not invented here.

## Booking and email

Set `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`, and `SMTP_PORT=587` for STARTTLS patient email verification. Without SMTP or `BOOKING_ENCRYPTION_KEY`, verification/booking returns a configuration error. SMTP credentials are server-only. SES SMTP credentials can be used after configuring the sender and recipient access in your AWS account.

**Clinic notifications and clinic OTP delivery remain suppressed, as in the source.** The React UI explicitly labels this testing mode. Requests can be recorded and tracked, but are not delivered or confirmed appointments. Enabling delivery requires a separate implementation and verification. No preview code, clinic URL or notification-content endpoint is exposed by the hosted API.

## AWS deployment

See [AWS deployment instructions](docs/AWS.md). Includes a non-root Docker image, Gunicorn and Elastic Beanstalk configuration. No AWS resources are created by this repository, and no deployment or live data migration happens automatically.

## Tests

```sh
python -m unittest discover -v
python manage.py check
```

The local suite includes Django client integration tests for account registration/login, CSRF, cookie security, cross-account isolation, patient email verification, booking idempotency and tracking. SMTP is mocked only in tests. Tests use synthetic contacts and temporary SQLite files.

GitHub Actions additionally runs migrations and API tests against a fresh PostgreSQL 17 service, a production deployment settings check, and a Docker build. **`API_TEST_DATABASE_URL` and `TEST_DATABASE_URL` must only point at disposable test databases. Tests delete their data. Never use a live URL.** Local Windows verification cannot start PostgreSQL because the machine's application-control policy blocks its executable; PostgreSQL and Docker checks must pass in CI before deployment.

## Existing live data

No live database was accessed or changed. Runtime startup does not seed or migrate. The Django initial migration creates missing tables and leaves existing application rows intact, but it is not permission to run against production. The source repository requires Vikram-approved change records, committed exact SQL, a separate branch-copy test, and a verified backup before live writes. Preserve that workflow and keep directory sync paused. The separate `directory-data` branch remains in the source repository; it is not silently replaced by these code repositories. Do not use `seed_demo` to reproduce live data. Plan and verify an explicit backup/restore or database cutover under the existing policy.

Original source informational pages are retained in the frontend. Application/data, environment files, SMTP credentials and encryption keys are excluded from Git.
