# Directory response performance

The API retains the same search/filter/booking contracts and browser-local location sorting.

- Summary and metadata read projected geography columns and counts, not complete center payloads. PostgreSQL performs the JSON projection before sending rows to the application.
- Parsed public directory snapshots are cached per worker for 30 seconds, with at most eight snapshots. The current published batch is checked on every read, so publishing or rollback changes the version immediately. Each caller receives an independent copy: per-search annotations cannot mutate a later result. No account, session, OTP, booking or admin response is cached. In-place edits within an existing PostgreSQL batch can take up to 30 seconds to appear; use the existing publish workflow for immediate version changes.
- PostgreSQL domain connections are reused in a bounded, worker-local pool. Normal context exit commits; exceptions roll back before the connection returns to the pool. Prepared statements remain disabled for upstream transaction-pool compatibility. The default is four domain connections per application worker; `SELFPAY_DB_POOL_SIZE=0` restores the original non-pooled connection behavior. Size this alongside worker count and Django ORM connections.
- Only large directory/search/spatial responses are gzip-compressed when requested by the client. Existing private/no-store headers remain in place. Auth, verification, booking and admin responses are not compressed by this middleware.
- Frontend metadata starts as soon as a browser session is known, independently of summary loading. Overlapping summary/metadata GETs share one in-flight request, scoped to the session version, and are discarded after completion. Mutations and private account responses are never shared or automatically retried.

## AWS update

Install the new pool dependency before restarting:

```bash
cd /var/www/MyselfPay_Backend
sudo git pull --ff-only origin codex/mobile-username-accounts
sudo ./venv/bin/python -m pip install -r requirements.lock
sudo ./venv/bin/python manage.py migrate --noinput
sudo ./venv/bin/python manage.py check
sudo systemctl restart myselfpay-backend.service
sudo systemctl status myselfpay-backend.service --no-pager
```

Compare cold and repeated API timings after the restart. Network distance, AWS instance load, database placement and response size still affect actual production latency. Local fixture benchmarks do not prove production response times. Search still returns the full matching directory for private browser-side distance sorting and pagination; compression reduces transfer size without changing which centers are included.
