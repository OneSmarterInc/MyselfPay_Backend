# Center Pro integration mapping

Local integration of delivery branch codex/initial-implementation into existing MySelfPay. No second database, no production deployment, no demo seeds. Source remains in the separate repositories for comparison.

| Source | Existing identity | Integration |
|---|---|---|
| Center | active entries.entry_key | Center.directory_key unique; directory remains authoritative |
| User | Django auth_user for staff/admin | Reuse existing matching email or admin-assigned account; no patient-account conversion |
| Center login | selfpay_center cookie, center_portal_sessions | Validate existing cookie each request, map verified email to an explicitly assigned membership |
| Admin login | existing Django password/TOTP | Reuse admin_totp.verified; no second enrollment or password form |
| Offer | active offers.id | Offer.directory_offer_id unique mapping, immutable old snapshots |
| Request | booking_requests.id | BookingRequest.legacy_id and reference stable; queue imports idempotently, confirmations update existing patient request |
| Resource/hours | no equivalent existing stable resource model | Add scoped Center Pro slots and hours in the same database; never invent real availability |
| Payment/completed scans | no authoritative source connected | Keep unavailable, never derive revenue from appointment totals |

New tables use the center_pro prefix. Existing directory and request tables are not renamed or replaced. Center Pro can be opened from the existing center login and from Admin > Center workspace. Admin assigns owner/staff roles; verified mailbox possession alone does not grant owner access. No sample role login is enabled.

Public patient/referral links use #center-pro/patient and #center-pro/referral. Existing patient login, requests, Secure Mail and admin functions remain available. Estimate documents are drafts; billing, SMS link delivery and forecasts retain explicit unavailable states. Existing patient preferences remain compatibility inputs; new Center Pro referrals use exactly three periods and saved hours.

## Local review

Open http://localhost:5173/#center through the existing center login. Existing administrators sign in at http://localhost:5173/admin/ and choose **Center workspace & access**. The administrator searches the directory center, opens assignments, and assigns the real email as owner or staff. Existing verified center emails then map to those assignments. Staff not listed as the directory contact can use the secure email-link option on the existing Center login popup. New staff accounts have unusable passwords; no sample account is installed.

The directory mapping command is `python manage.py sync_center_pro`; rerunning preserves IDs and accounts. Requests refresh from the original booking store as a permitted center workspace is read. Staff must save real working hours and add real scanner/room slots before confirming requests. The exact slot must fit a submitted period and current hours; a center lock and overlap check protect writes on PostgreSQL. Local SQLite checks do not certify PostgreSQL concurrency.

Owner price changes create the existing admin price-review submissions. They do not bypass the public approval queue. Existing numerical offers are mapped; quote-only directory entries are not turned into invented payable offers. Quote-only imported requests retain a null numerical price. Cancellations/closed original requests appear closed and cannot be confirmed.

External scope retained from handoff: SMS self-pay link delivery, completed-scan/revenue feeds, automatic market/outreach sourcing, subscriptions, digest delivery and issued GFE documents are not connected. Draft estimates, link generation and saved outreach actions remain functional. SMTP magic links use the existing admin SMTP configuration; no email was sent during verification. Admin MFA recovery continues through the existing MySelfPay implementation, not the standalone module's profile.

Frontend module styles are scoped under `.center-pro-root`; source routes are namespaced under `#center-pro/`. Existing patient routes and center listing/contact/inbox settings remain available (`#center-legacy`). Local database backup is under `.local/center-pro-backups/`. No production DB change, push or deployment is part of this integration.

## Dual admin and center roles

An active existing administrator email may also receive an explicit owner/staff membership. Email link verification establishes a center-only session and removes any retained admin TOTP proof. Admin APIs still require the existing password/TOTP proof and admin workspace mode. A verified administrator with an assigned center can switch between workspaces. `workspace` selects among already verified identities; it never grants a role. New tests cover real Django admin cookies, dual-role assignments, and denial of admin access after center magic-link login.

## Native administrator page

`/admin/center-pro/` now renders inside the existing Django MySelfPay admin navbar/sidebar; it no longer redirects to a separate React workspace. Search and paginated center rows, owner/staff assignments, confirmation before revocation and organization audit are available in this page. Native forms and the API call the same transactional access services. Existing password/TOTP and CSRF protection are unchanged. Desktop and phone walkthroughs verify the URL stays under `/admin/` and no page-wide overflow occurs.


## Deploying the integrated workspace

After updating the backend checkout to main, run in the configured production environment:

```sh
python manage.py migrate --noinput
python manage.py sync_center_pro
python manage.py collectstatic --noinput
```

Restart the existing backend service using the normal deployment process. Deploy the frontend main build after the backend is ready. Existing SMTP settings deliver the single-use email sign-in links; no OTP center-login screen is used by the integrated frontend. The main administrator still signs in with the existing password and TOTP, then assigns owner/staff access at `/admin/center-pro/`.

Owners and staff may confirm an alternative appointment after patient agreement, with resource overlap checks. This explicit action can override saved hours and preferred periods; ordinary slot selection still validates them. Local database contents and credentials are never included in Git.


## Default directory owner email

When requesting an email sign-in link, an exact match to an active directory listing email is eligible for default owner membership for those listings. New accounts have unusable passwords, and access still requires the emailed single-use link. Existing staff memberships remain staff. Inactive or ambiguous duplicate-email accounts require administrator attention. Centers with an access-revoked audit record stay manual-only so removed access is not silently recreated. Main admin can explicitly assign those memberships again.

This update needs a backend restart after pulling main; it adds no migration and requires no bulk account seed.
