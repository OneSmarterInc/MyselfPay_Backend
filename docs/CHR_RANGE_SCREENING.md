# CHR lower-bound screening

All imported state hospital summaries share chr_ranges.hospital_summary. Requests include mri/ct center prices. Select a positive MRI price first, otherwise positive CT price, otherwise $100. Each lower bound is the minimum stored numeric value strictly above this threshold. Cash/gross use individual source prices; insurer uses the stored numeric endpoints. Original cash, gross and insurer maxima are unchanged. A hospital is hidden if any of the three qualifying minima is missing. No values are clamped or invented, and no source data is modified.

The summary is a screened subset of source prices; it does not mean all of a hospital's prices exceed the center price. State datasets remain grouped across source exam rows. Screened lower bounds and unchanged maxima drive both the chart and table. All current public/program/owner CHR views send the same center price references.

Ohio CHR requests use view=filtered, preserving existing hospital/procedure eligibility and the 50-mile radius. The query screens the stored procedure minima/medians/maxima, preserves the previous grouped upper values and applies one MRI/CT/default threshold across tabs. Legacy API calls without view=filtered retain their existing response behavior. Distance columns are removed from CHR and hospital-comparison tables; location selection and geographic filtering are unchanged.

Deploy backend main on AWS and restart myselfpay-backend.service. Deploy frontend main on Vercel. No migration, CSV import, or source-table rebuild is needed.
