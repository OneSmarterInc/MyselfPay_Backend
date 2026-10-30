# Deploy Django on AWS Elastic Beanstalk + RDS PostgreSQL

The backend is packaged for an AWS **Docker on Amazon Linux 2023** Elastic Beanstalk environment. The same Docker image can run in ECS/Fargate with an ALB. This guide does not provision AWS resources or claim an AWS deployment has been tested.

1. Create a dedicated RDS PostgreSQL database in private subnets with automated backups and encryption. Permit port 5432 only from the application security group. Keep the database separate from the Beanstalk environment lifecycle. Use a separate least-privilege application role after initial schema setup; keep owner credentials out of application runtime.
2. Create a load-balanced Beanstalk Docker environment. The root `Dockerfile` and `Dockerrun.aws.json` use container port 8000. The application runs as a non-root user with Gunicorn. Configure an ACM certificate and HTTPS listener on the ALB; redirect public HTTP to HTTPS. Allow application traffic only through the managed proxy/ALB. The proxy must overwrite `X-Forwarded-Proto`; do not expose the container port publicly.
3. Configure environment variables through your deployment's secret mechanism. Do not bake credentials into the image or ZIP:

```text
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<at-least-50-random-characters>
DJANGO_ALLOWED_HOSTS=api.your-domain.com,127.0.0.1
FRONTEND_ORIGIN=https://app.your-domain.com
DATABASE_URL=postgresql://app_user:<url-encoded-password>@<rds-endpoint>:5432/selfpay?sslmode=require
BOOKING_ENCRYPTION_KEY=<stable-generated-Fernet-key>
COOKIE_SAMESITE=Lax
SMTP_HOST=<your-SMTP-or-SES-SMTP-host>
SMTP_PORT=587
SMTP_USERNAME=<SMTP-user>
SMTP_PASSWORD=<SMTP-password>
SMTP_FROM=<verified-sender>
```

Prefer frontend `app.your-domain.com` and backend `api.your-domain.com` under the same parent domain. Set the frontend's build variable `VITE_API_BASE_URL=https://api.your-domain.com/api`. Django then accepts the frontend origin, cookies and CSRF header. `COOKIE_SAMESITE=None` is available for HTTPS cross-site deployments, but browser third-party-cookie restrictions can still prevent sessions; use a shared parent domain or a same-origin `/api` reverse proxy. Never put secrets in `VITE_` variables.

4. **For a new empty database only**, run the following as a one-off release task with the migration role, using the same image and environment:

```sh
python manage.py migrate --noinput
python manage.py check --deploy --fail-level WARNING
```

Do not run migrations in every container startup. Do not enable demo seeding on production. Load the approved directory through a reviewed import/cutover process. For an existing live SelfPay/Neon database, first satisfy the source repository's change-record policy; do not run these commands until the exact migration effects are reviewed, branch-tested and approved, and a backup is verified.

5. Zip the repository contents with `Dockerfile` at the archive root, excluding `.git`, `.env`, local databases and test output. Upload this source bundle to Beanstalk. Set the ALB health check path to `/api/health` with expected status 200. Django intentionally exempts this path from HTTPS redirects so private health probes work. The Docker health check uses `127.0.0.1`, which must be in `DJANGO_ALLOWED_HOSTS`.
6. Verify `/api/health`, guest search, signup/login, CSRF rejection, sign-out, source directory counts and email verification on the deployed HTTPS domains. An empty directory is not an import success. Clinic delivery is still intentionally suppressed: the interface must continue to say so. Verify restore capability and protect the retained encryption key before any real booking use.

## Operational notes

- API responses are no-store. Do not put request bodies, cookies or booking capability tokens in logs. Gunicorn access logs are disabled; configure ALB/WAF logging carefully so sensitive requests and identifiers are excluded/redacted.
- Per-session, account and global guest quotas are stored in PostgreSQL. Add edge rate limiting appropriate to your deployment; the application does not blindly trust client-supplied forwarding headers for identity.
- Schedule retention cleanup through a reviewed management job with a restricted database role. Existing source cleanup automation is not activated by this migration. Maintain backups separately from data retention jobs.
- Keep schema owner access separate from runtime credentials. Configure RDS CA certificates and `sslmode=verify-full` where your database infrastructure supports it.
- Official references: [AWS Docker source bundle configuration](https://docs.aws.amazon.com/elasticbeanstalk/latest/dg/single-container-docker-configuration.html), [Django deployment checklist](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/).
