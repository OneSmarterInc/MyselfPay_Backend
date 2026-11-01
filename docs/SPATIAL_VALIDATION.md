# Spatial implementation verification

Verified locally on 2026-11-01 using Python 3.12, Django 5.2, H3 4.5 and a disposable PostgreSQL 18 instance bound to localhost. No AWS database was accessed or changed. All users, locations used for movement simulation and email tests were synthetic; no live SMTP test was sent.

| Check | Result |
| --- | --- |
| Complete backend domain/API suite with PostgreSQL-only tests enabled | 119 passed, no skips |
| Frontend unit suite | 27 passed |
| Complete Playwright browser suite | 17 passed |
| Fresh PostgreSQL migrations 0001 through 0004 | Applied successfully |
| Repeated migrate | No migrations to apply |
| Django makemigrations check | No changes detected |
| Django production security check | No issues |
| Vite production build | Passed; H3 in a separate reusable chunk |

Browser tests cover actual browser location callbacks → cookie-authenticated H3-only request → PostgreSQL-compatible indexed service (SQLite-backed isolated browser fixture) → locally sorted cards. Separate tests verify the same API and SQL index on real PostgreSQL. Movement within one coarse cell makes one request; crossing into a new cell makes a second request. The network assertions confirm that raw GPS and the user's H3 11 are absent from API request URLs/bodies and that GPS does not download the full catalog. Permission denial falls back to manual search. Transient device errors can recover without restarting the watch.

Registration tests check ZIP+4 format, account persistence and automatic base-ZIP search on both signup and subsequent login. Existing browser tests cover manual filters, pagination, original desktop/mobile layout, comparison, detail pages, login-required booking UI, chat presets and session expiry. The intended toolbar location button is reflected in the search UI fixture.

The database test uses EXPLAIN to verify the composite H3 index and checks source payloads/hashes before and after repeated backfills. This proves index selection and record preservation on test data; it is not a production latency benchmark. One-ring coverage and the 500-site cap are explicit boundaries. See SPATIAL_ARCHITECTURE.md for remaining precision, cache freshness, dense-area and native-platform limitations.

The initial implementation plan is in SPATIAL_PLAN.md. The supplied design reference is preserved as Zero-Egress_H3_Spatial_Architecture_Guide.md. Production AWS installation and real-device GPS accuracy remain deployment checks for the server operator.
