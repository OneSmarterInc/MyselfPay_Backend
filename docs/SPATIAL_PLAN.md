Implementation plan

Existing architecture: React App.jsx owns state and renders original HTML templates. api.js owns same-origin API requests and cookie/CSRF sessions. DRF uses IsAuthenticated (including explicit guest sessions), with PostgreSQL SQL services rather than ORM domain models. Entries have JSON locations; geography currently derives city/ZIP reference points. Search and booking must remain intact.

1. Finish optional ZIP+4 account persistence and automatic base-ZIP login search.
2. Add a derived spatial_locations projection referencing existing entries, with stored H3 7/11 fields and a B-tree (batch_id,h3_res7) index. Map it using an unmanaged Django model. Preserve source payloads, mapping hashes and original records. Index ingestion transactionally; backfill with a resumable bounded command, never during an API read.
3. Add a session-protected, bounded bundle GET route. Query only the active batch and grid_disk(cell,1). Allowlist directory metadata; no account or booking data. Keep authenticated responses private/no-store; Cloudflare remains optional.
4. Add browser-only H3/geolocation helpers and an opt-in search control. Cache at most eight bundles in memory with a five-minute lifetime, coalesce pending requests, ignore obsolete responses and discard on sign-out. Use H3 11 grid steps locally and Haversine miles for actual geographic sorting/fallback. No precise coordinates or microcells in requests/storage/logs.
5. Keep existing card UI, comparison, booking and 10-card pagination. Reuse local results for filter changes, stop location watch on hidden/unmount, preserve manual state/ZIP search.
6. Test ingestion, migration/backfill, indexed query boundaries, permissions, validation, caching and browser location flow, then publish source and document AWS migration before frontend activation.

Adaptations to supplied guide: B-tree lookup is indexed, not a guarantee of O(1) or sub-millisecond latency. H3 7 is approximate location, not anonymity. Pentagon disks may have fewer than seven cells. Browser Geolocation cannot use native activity recognition. Existing imported city/ZIP coordinates are approximate and retain their precision labels. Authenticated responses are never shared-CDN cached.
