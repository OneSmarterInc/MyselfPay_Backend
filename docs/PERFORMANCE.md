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

## Approval and activity update follow-up

Profile/contact approvals and price approvals now clone ready spatial index records in one SQL copy, rebuilding only edited locations. A source without the readiness marker still uses the full index build. The directory snapshot, readiness marker and publication remain inside the original transaction; a failed rebuild rolls back the change. The previous published batch remains available for rollback.

For 100 synthetic map records, the map-index portion used 203 database statements before and 4 after (5.81 ms versus 1.29 ms locally, with H3 conversion stubbed). This measures the indexing portion, not end-to-end AWS approval time. Review pages now load center names with one LEFT JOIN instead of a query per submission: 2 total reads for 100 profile/price review rows rather than 102.

Activity saves return the updated target with the saved activity. The client applies this confirmed result directly and refreshes activity history, removing the redundant target GET. No optimistic success or private response cache is used.

Validation: 143 backend tests covering approval, portal, spatial and center flows; 125 frontend tests; production build; desktop/mobile activity-save checks. The local Windows H3 adapter lacks two methods, so spatial tests use the project's installed h3-js for those methods. No schema migration, CSV import or extra worker is required. Deploy both backend and frontend main using the commands above.
