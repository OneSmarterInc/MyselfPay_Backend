# MySelfPay price scraper pilot

## Local review page

Start the page from the repository root.

```powershell
.\.venv\Scripts\python.exe -m scrapper.ui
```

On Linux or macOS, use `python -m scrapper.ui` in the active virtual environment.
Open the URL printed in the terminal (usually `http://127.0.0.1:8765/`). Upload
the same CSV shown below. An approved baseline JSON is optional. The page shows
prices, changes, evidence and source issues, and can download the report.
The terminal must stay open while the page is in use.

## Command line

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

The two pilot providers have dedicated parsers. Other public HTTPS HTML pricing
URLs use a generic table, list, paragraph and heading-based parser. The report
includes these rows in `review_candidates` with evidence, source, observation
time, and page-level contact details where available. An isolated numeric cash
column in a simple table can supply a candidate amount. Review the exact
branch, fee inclusions and protocol before using a candidate. PDF schedules,
JavaScript-only pages and unrecognized layouts require review. Current baseline
comparison operates on structured offers from the dedicated parsers.

The command writes review output to stdout. PostgreSQL, the admin interface,
scheduled checks and publication are subsequent integration work. Resolve each
pilot location against the existing directory before writing to `entries` or `offers`.
