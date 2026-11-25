# Shared screen performance changes

No schema migrations, data import, or new environment settings are required.

- Center-only routes load independently from the public app. They no longer initialize public account, directory metadata or search requests. Public and center navigation still uses the existing URLs.
- Public search uses one batch-versioned catalog snapshot for both filtering passes. Each request retains its own isolated copy; no account data or mutation responses are cached.
- Center price comparison reuses the same public catalog, retains exact exam/scope/contrast/type matching, and calculates comparisons only for the requested page. Active membership is checked on every request.
- Booking synchronization runs on booking/scheduling/reporting screens rather than price, audit, scan or subscription screens. Existing imports are loaded in bulk; unchanged rows skip redundant decryption and ORM lookups. New requests and status changes are still synchronized on every booking read.
- Overview counts use one database aggregate. Admin center access counts are computed for the current 20-row page instead of grouping the entire center directory before pagination.

## Local measurements

Same local dataset and query (ZIP 45123, statewide), three samples; these are server-side measurements, not production network guarantees.

| Operation | Before (ms) | After (ms) |
| --- | --- | --- |
| Public search, cold | 1496 | 819 |
| Public search, warm | 486 / 1057 | 224 / 278 |
| Center directory prices | 795 / 807 / 720 | 205 / 201 / 255 |

The bulk synchronization regression covers 100 unchanged requests with one ORM read and no decryption. Browser checks cover desktop/mobile workspace startup and public-to-center navigation.

## AWS rollout

```bash
cd /var/www/MyselfPay_Backend
source venv/bin/activate
git pull --ff-only origin main &&
python manage.py check &&
sudo systemctl restart myselfpay-backend.service &&
sudo systemctl status myselfpay-backend.service --no-pager
```

Deploy latest frontend main on Vercel (automatic Git deployment, or manual if disabled). No CSV or migration is needed.
