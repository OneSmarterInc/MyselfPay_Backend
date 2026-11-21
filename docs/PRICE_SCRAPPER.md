# MySelfPay price scraper pilot

Run the pilot from the backend checkout after installing the repository dependencies.
Create a CSV containing these inputs.

```csv
pricing_url
https://oncallimaging.net/pricing/
https://pineywoodsimaging.com/pricing/
```

```sh
python -m scrapper urls.csv > first-check.json
python -m scrapper urls.csv --baseline approved.json > next-check.json
python -m unittest tests.test_scrapper -v
```

Review the first check against the provider pages. Create `approved.json` by keeping
the approved rows in the report's `offers` array. Keep this file for later checks.
The report contains center details, observed offers, new or changed offers, and source
issues. A run with source issues still writes the JSON report and exits with status 1.

The match uses the location, modality, body area, contrast protocol and payment
option. A price, price type or payment-terms change is recorded beside the approved
offer. Evidence and timestamps are retained for review. Source failures do not create
price-change findings.

The pilot uses exact configured HTTPS URLs and rejects redirects. Each parser targets
the provider's routine MRI and CT cash prices. The Pineywoods parser also handles
abdomen/pelvis CT. General price rows do not assert a specific body part.
`both` means the combined without-and-with contrast protocol. Facility and reading fee
inclusions need separate evidence. Other providers, PDFs, hospital transparency
files, screening prices and add-ons are outside the pilot.

The command writes review output to stdout. PostgreSQL, the admin interface,
scheduled checks and publication are subsequent integration work. Resolve each
pilot location against the existing directory before writing to `entries` or `offers`.
