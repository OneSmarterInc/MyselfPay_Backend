# Owner-managed staff access

The owner workspace includes Manage staff. Owners can list/search staff assigned to the current center, add an active/new account by work email, and revoke staff access after confirmation. Staff use the existing single-use email-link login; assigning access does not send an invitation email.

Routes:
- GET/POST `/api/center-pro/centers/<center_id>/staff`
- POST `/api/center-pro/centers/<center_id>/staff/<membership_id>/revoke`

Every request checks active center membership and owner role. Mutations recheck ownership while holding the center lock. The assigned role is always staff, regardless of submitted extra fields. Owners cannot remove owner memberships through these routes, alter access to another center, or disable the user's global account. Duplicate assignments and inactive/ambiguous accounts are rejected. Existing access audit events are recorded. CSRF applies to cookie-authenticated writes.

No migrations or new environment variables are required. Pull backend main and restart myselfpay-backend.service on AWS. Deploy frontend main through Vercel.
