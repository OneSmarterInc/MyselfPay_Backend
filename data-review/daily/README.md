# Daily MySelfPay review CSVs

This folder contains dated, official-source research batches. Filenames use `YYYY-MM-DD.csv` based on the user's UTC+05:30 calendar date. Uploading a file is not approval to import or publish its rows.

## 2026-11-06

- **5 newly researched records:** four in North Dakota and one in Wyoming.
- All five have official evidence for the physical facility, MRI/CT service, a radiology contact and an estimate or price-transparency route.
- Four additional candidates were held outside the CSV: Pembina County Memorial Hospital, Northwood Deaconess Health Center and Dakota Regional Medical Center need a verified radiology-specific contact route; Hot Springs Health needs explicit current official MRI/CT evidence.
- The 18 earlier November 6 proposals are not republished or counted as new discoveries.
- No scan-specific numeric MRI/CT cash prices were verified. `Quote required` is not a price offer. Unknown information remains blank or explicitly unknown.
- Published emails are scoped to their actual purpose. A pricing-team or named radiology contact is not automatically an appointment inbox. `Current usable email` remains blank where an appointment/general-inquiry route has not been established.
- This initial batch does **not** meet the requested 100+ daily target. Rows were not invented or padded to reach a count.

## Columns and safe use

The CSV has a real header row, UTF-8 encoding and 67 columns. Its first 52 columns preserve the canonical directory schema and order. The additional 15 fields record the batch, discovery status, modalities, location/address sources, order-policy uncertainty, price verification, duplicate-check scope, review recommendation and approval/import status.

Every row has `Record category = Pending`, a stable source record ID and `Import permitted = No`. A recommendation to review is not user approval. Existing row approval and live database change-record requirements still apply.

The current backend `core.read_source` accepts JSON and XLSX, **not CSV**. This file is a research/review artifact; it must not be passed directly to that importer. No parser, application code, canonical workbook, existing repository CSV or live database was changed by this upload.

## Duplicate checks and limits

Checked normalized names, city/state, street addresses, phones and relevant aliases against canonical v59 (5,404 total rows, including 5,021 Main), prior approval/research batches, all 18 earlier November 6 proposals, the active 2,900-row live directory, and the existing 5,390-row repository CSV. The active live snapshot hash was rechecked unchanged on November 6. No duplicate identity was found in these checks.

The live directory has 2,755 entries without structured locations. Legacy names and coverage text were considered, but final address reconciliation is still required before import. This is not an absolute guarantee that every real-world alias or relocated facility has been resolved.

First Care's direct radiology number is newly verified on its official contact page. Langdon's former Cavalier County Memorial Hospital name and Heart of America's former Rugby address were checked. All research URLs and verification dates are in the CSV.

## Daily append convention

Add a new dated CSV only after rechecking current canonical, live and prior daily/review batches. Do not overwrite an existing same-date file blindly. Reconcile an interrupted run, preserve stable IDs, and report the actual qualifying count. Keep unresolved or weakly supported candidates out of the daily qualified batch. Do not merge, import or deploy without the applicable approval.
