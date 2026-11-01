# Private spatial search in MySelfPay

This implements the supplied `Zero-Egress_H3_Spatial_Architecture_Guide.md` within the existing React, Django REST Framework and PostgreSQL application. The guide is a design reference, not a reason to replace DRF with its FastAPI example. Existing manual search, directory, comparison, booking and session features remain available.

## Existing architecture and decisions

React `App.jsx` owns routing, account state and original UI templates. `api.js` owns the configured API prefix, HttpOnly-cookie sessions, CSRF and timeouts. The backend already uses DRF resources over SQL domain services. Facility locations are arrays inside immutable, batch-versioned `entries.payload` JSON; there was no existing Django location model to extend.

`spatial_locations` is a rebuildable projection of those existing locations, not a second source of facility data. Each row references an existing `(batch_id,entry_key)` with a cascading foreign key and stores both H3 resolutions, ordinal and original precision. An unmanaged `SpatialLocation` model describes the existing SQL persistence convention. The `(batch_id,h3_res7)` B-tree index serves the bundle lookup; `(batch_id,entry_key)` indexes offer lookup. No PostGIS extension or database-side H3 extension is required.

H3 conversion happens on import and backfill, never on bundle reads. Source entries, source hashes, reviewed price mappings and old batches are preserved. The normal `core.stage_import` path and synthetic-center publishing path index the new batch transactionally. Custom direct-SQL imports must run `backfill_spatial` afterward. Only the active published batch is queried.

## User flow

Registration accepts an optional ZIP+4 value such as `45202-1234`. Server and browser validate five ASCII digits, a hyphen and four digits. The value is stored separately from the original account schema. Login and `/api/me` return it only for the account owner. After registration/login, the first five digits initiate the existing statewide search, sorted by distance while retaining complete-card priority. Pending appointment login resumes the appointment instead. ZIP-area distances are approximate; ZIP+4 is not used to claim street-level accuracy. Older accounts can continue manual search without supplying a ZIP.

The search toolbar also offers **Use my location** once the server reports a completed spatial index. It requests browser permission only after the click. Browser coordinates are converted locally to H3 7 and H3 11. Only H3 7 enters the URL. The browser watches location while search is visible and updates proximity locally. **Stop location updates**, leaving the search view, hiding the page, permission denial, an API error or unmount releases the watch. Transient device unavailability/timeouts display a message while allowing the browser to deliver a fresh fix. Hidden pages do not automatically resume it. Manual location edits switch back to the existing search.

The local manager deduplicates concurrent requests. Updates within the active H3 7 cell reuse its bundle without another request. An eight-entry in-memory cache reuses visited cells for up to five minutes. It contains facility bundles, not raw user GPS. Session changes and explicit stop clear it, and late responses cannot repopulate a cleared session. An active bundle remains stable until its cell changes or the user stops/restarts location updates. Stop/restart provides an explicit refresh after catalog changes.

Optional service, contrast, budget and price-type filters run locally over the bundle. Budgets require a reviewed mapped numeric offer; unreviewed raw price wording does not become a guaranteed numeric quote. Body/exam text remains informational, matching existing manual-search behavior. Mileage selection does not expand the H3 neighborhood. Use ZIP/state/National search to browse a wider region. Multiple branches of the same entry use the closest available branch. Reviewed numeric offers are attached only where the existing offer-location evidence supports that site. The existing complete-card priority and ten-card pagination remain.

## API

`GET /api/v1/spatial-bundle/{h3_res7}/`

Use the existing session cookie, including an explicitly created guest session. Authentication is checked before serving a bundle. Mutating endpoints retain their existing CSRF rules. No new public unauthenticated data endpoint is introduced.

The input must be a canonical lowercase, valid H3 cell of exactly resolution 7. Query parameters are rejected, including coordinate parameters. `grid_disk(cell,1)` determines the queried cells. A hexagon normally yields seven cells; pentagon neighborhoods can yield fewer. The API does not promise a fixed radius in miles or exhaustive nearest-provider coverage beyond the returned cells.

Example shape:

```json
{
  "center_cell": "872a930f5ffffff",
  "queried_cells": ["872a930f5ffffff"],
  "count": 1,
  "version": "published-batch-id",
  "limit": 500,
  "truncated": false,
  "offers_truncated": false,
  "records": [{
    "id": "stable-projection-id",
    "entry_key": "existing-directory-key",
    "h3_res7": "872a930f5ffffff",
    "h3_res11": "8b2a930f5800fff",
    "location": {
      "latitude": 39.1,
      "longitude": -84.5,
      "precision": "city"
    },
    "entry": {"name": "Facility name", "booking_id": "existing-directory-key"},
    "offers": []
  }]
}
```

This illustrates the schema, not a complete real geographic response. `queried_cells` in a real response contains the entire disk and each facility's cell IDs must match its stored coordinates. Facility coordinates and H3 11 IDs are returned; the user's coordinates and H3 11 ID are never submitted.

The API performs bounded indexed reads and caps returned site records at 500 and mapped offers at 1,000 per bundle. `count` is the total matching location rows, not unique centers. `truncated` and `offers_truncated` explicitly warn when some records are omitted; the UI directs users to wider manual search. Ordering the capped server subset by ID does not guarantee the globally nearest 500 sites in extremely dense cells.

Status codes: 200 for a valid bundle, including empty results; 400 for invalid cells or unexpected parameters; 401 without a valid session; 429 above 60 bundle requests per account/guest session per minute; 503 if a published batch is not indexed or a service is unavailable. Not-ready responses include `Retry-After: 60`. Requests do not trigger a whole-directory backfill.

## Privacy and caching

Exact user latitude/longitude and the user's H3 11 remain transient client-side values. They are not sent to the API, put in local/session storage, attached to URLs, saved in conversations, or logged by this feature. The normal API wrapper continues using the configured same-origin `/api` prefix. Facility maps/directions use facility information, not the user's GPS. No new external mapping, analytics, geocoding or speech service is introduced.

H3 7 is approximate location, not anonymity. Server/proxy access logs may contain this coarse cell URL. Production requires HTTPS through the existing Django security settings and Vercel rewrite. Browser/OS location services remain subject to the user's platform behavior.

Every spatial response, including errors, receives `Cache-Control: private, no-store`. Successful responses also vary by Cookie and Origin. Do not enable Cloudflare or CloudFront "cache everything" rules on these authenticated paths. No Cloudflare configuration was present or required. Browser reuse is an explicit bounded in-memory application cache, not a public HTTP cache. A future public-CDN variant must use a separate nonpersonal public data contract and explicit versioned keys/invalidation.

H3 grid steps are stored only as a local proximity metric/tie-breaker; they are never displayed as meters, miles or kilometers. Haversine yields straight-line miles from the device to the facility's stored reference point and remains the physical-distance sort, including when H3 grid traversal fails near pentagons or unsupported long traversals. City/ZIP precision remains labeled. Indexed lookups do not guarantee O(1), sub-millisecond latency, exact GPS accuracy or driving time. Native iOS/Android activity-recognition APIs, Wi-Fi RTT, drive-time routing and carrier location are outside this browser implementation.

## Local installation and run

Backend, from the existing backend checkout:

```bash
./venv/bin/python -m pip install -r requirements.lock
./venv/bin/python manage.py migrate --noinput
./venv/bin/python manage.py backfill_spatial
./venv/bin/python manage.py check
./venv/bin/python manage.py runserver
```

Use the existing PostgreSQL `DATABASE_URL`, `DJANGO_SECRET_KEY`, hosts and `FRONTEND_ORIGIN`. No new secrets are needed. SQLite remains an isolated development/test option already supported by the project, not a production database replacement. New local installations may run `seed_demo` after migration instead of backfilling an empty database. Never seed a production database.

Frontend:

```bash
npm ci
npm test
npm run build
npm run dev
```

Keep the existing `VITE_API_BASE_URL=/api` and local proxy configuration. Geolocation works on HTTPS or supported localhost secure contexts.

## AWS and Vercel rollout

Back up the existing PostgreSQL database using your normal backup procedure before schema changes. Migrations are additive. Migration `0003_account_zip` creates optional account ZIP storage; `0004_spatial` creates the location projection/index and model state. They do not delete original records or change the active batch. Their reverse is intentionally non-destructive; rollback the application rather than dropping preserved data.

From the AWS backend directory:

```bash
cd /var/www/MyselfPay_Backend
git pull --ff-only origin main
./venv/bin/python -m pip install -r requirements.lock
./venv/bin/python manage.py migrate --noinput
./venv/bin/python manage.py backfill_spatial
./venv/bin/python manage.py check
sudo systemctl restart myselfpay-backend.service
```

If Git reports local changes to `core.py`, preserve and review that diff before merging the import hook. Do not discard the server's custom import work or use a forced reset. The projection backfill reads up to 200 source rows per transaction, can be rerun after interruption, and marks each completed batch ready. It temporarily disables new spatial reads for the batch being rebuilt. Run one backfill at a time during a maintenance window; do not edit the same immutable source batch concurrently. The first index build is deliberately separate from migration so deployment is not blocked by an unbounded data migration. For very large installations, schedule maintenance and monitor lock/build time for the initial index creation.

Verify `/api/health` is healthy and `/api/directory-summary` reports `spatial_ready: true` and `spatial_api_version: 1`. New frontend controls are capability-gated: ZIP registration appears when `account_zip_enabled` is advertised, and GPS appears only after the spatial index is ready. The metadata-only startup request waits for the new backend capability, preserving rollout compatibility. The legacy full catalog remains available only for explicit directory browsing; GPS searches never need it.

Deploy the frontend through the existing Vercel project with its current API rewrite. Refresh after backend rollout and verify a synthetic account's ZIP login search, location permission, local filters, comparison and login-required booking. No SMTP or encryption configuration changes are part of this work.

## Verification

`tests/test_spatial.py` covers canonical H3 validation, incorrect resolution, pentagon disk expansion, active-batch filtering, source preservation, idempotent backfill, the actual B-tree query plan, payload caps, safe metadata, authentication, cache headers and ZIP login persistence. The suite can target a disposable PostgreSQL database using `SPATIAL_TEST_DATABASE_URL`; never point test variables at production.

`tests/spatial.test.js` covers both H3 conversions, same-cell reuse, request coalescing, cache expiry/limits, late-response invalidation, permission failures, response consistency, local filters, geographic sorting, Haversine fallback and the absence of raw GPS in outgoing request arguments. `e2e/spatial.spec.js` walks browser location through the actual authenticated Django endpoint and exercises the manual fallback. Existing API, domain and UI suites remain part of verification.

Primary API references: [H3 indexing](https://h3geo.org/docs/api/indexing/), [H3 traversal](https://h3geo.org/docs/api/traversal/), [h3-py reference](https://uber.github.io/h3-py/api_verbose.html).

## Files

Backend additions: `spatial.py`, `api/models.py`, `api/spatial_views.py`, `api/migrations/0003_account_zip.py`, `api/migrations/0004_spatial.py`, `api/management/commands/backfill_spatial.py`, `ops/sql/20261101-account-zip.sql`, `ops/sql/20261101-spatial.sql`, `tests/test_spatial.py`, and this document. Backend changes: `accounts.py`, `core.py`, `api/views.py`, `api/serializers.py`, `api/middleware.py`, `config/urls.py`, `api/management/commands/add_test_center.py`, dependency files and the existing CI workflow.

Frontend additions: `src/spatial.js`, `tests/spatial.test.js`, `e2e/spatial.spec.js`, `docs/SPATIAL_SEARCH.md`. Frontend changes: `src/App.jsx`, `src/original-ui.js`, package manifests, `vite.config.js`, the signup/browser regression tests and the intentional search UI fixture.
