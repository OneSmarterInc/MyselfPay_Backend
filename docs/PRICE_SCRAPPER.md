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
**Prices by body part** groups observations by location, service and contrast.
A provider page without a confirmed location is grouped by its source URL. General rates,
special exams and excluded anatomy keep their original scope. The fixed body-part columns
include Knee, Brain, Lumbar spine, Shoulder, Abdomen & pelvis, Breast, Lung, Prostate and Whole body.
A dash means no mapped price in that cell. Open a price or variant count to see each exam,
payment context and source. Other exams retains observations outside these columns.

**Download overview CSV** exports the matrix. A unique offer supplies a numeric USD amount
(the lower bound for a range). The cell_details column gives price types, bounds and variants.
When a cell has several observations its numeric field stays blank and its details list them all.
**Download prices CSV** exports individual observations, including body_part, exam_variant and
exclusions. These are the rows shown under Offer detail.
The JSON report includes these in `pricing_rows`. Each row has numeric USD fields
`price_usd` and, for a range, `max_price_usd`. The price type distinguishes listed,
fixed, starting, range and add-on amounts. Unresolved text stays in the review report.
Rows from generic pages require confirmation of the provider, branch and terms.
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
python -m scrapper urls.csv --prices-csv prices.csv --overview-csv overview.csv > first-check.json
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
branch, fee inclusions and protocol before using a candidate. The supported interactive and document layouts are described below. Current baseline
comparison operates on structured offers from the dedicated parsers.

The command writes review output to stdout. PostgreSQL, the admin interface,
scheduled checks and publication are subsequent integration work. Resolve each
pilot location against the existing directory before writing to `entries` or `offers`.

## Database staging

Use a development or staging backend with the active directory loaded. Run Django
migrations there, then run `python manage.py stage_scraper_report scrapper-report.json`.
Each structured offer and candidate becomes a row in `scraper_observations`.
Prices use integer cents. An empty amount means the evidence still needs interpretation.
The original source row, URL and observation time stay with the normalized row.

Automatic assignment requires the exact directory source URL and normalized full
address of a unique single-location entry. Unmatched observations keep an empty
entry key. Inspect them with:

```sql
SELECT observation_id, source_url, exam, amount_cents, evidence
FROM scraper_observations WHERE entry_key IS NULL;
```

After checking the branch and offer scope, supply a JSON object mapping each reviewed
observation ID to its verified directory entry key using `--bindings bindings.json`.
Reimporting the same report reuses the same observations. Linking identifies a
center; publishing its price requires a separate approval and publication step.

## Published price tables and service menus

For the standalone Windows review tool, install the scraper dependencies in its
own environment and start the local UI from the repository root.

```powershell
.\.venv\Scripts\python.exe -m pip install -r scrapper/requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m scrapper.ui
```

The reader matches a provider's comparison-table column, follows service
options to their associated panels or same-site URLs, and reads explicit cash
columns from supported PDF and HTML schedules. The displayed exam total supplies
the amount. Separate contrast notes stay with the evidence. Provider estimates
use the price type `estimate`. If the server supplies an incomplete interactive
menu, Chromium can select visible services and read independently labelled results
in the same self-pay section. Browser requests are limited to same-origin GETs.
Forms requiring patient information, third-party widgets and additional types
of price layouts may need another adapter.

For linked image schedules with a ruled three-column grid, install Tesseract OCR
with English language data on the machine running the UI. The scraper checks
PATH and the standard Windows Tesseract-OCR installation directory. It compares
two OCR passes and retains matching exam/amount cells with their image URL and row.
If OCR is unavailable, the report identifies the linked schedule.

**Results by source** shows the input URL, numeric count and pages tried. A moved
link triggers a check of the provider homepage for replacement pricing links.
If a page opens in the admin's browser, **Use a saved provider page** accepts a
saved HTML file alongside its matching CSV URL. These observations carry
`evidence_origin=uploaded_html`. The timestamp records the review run. Verify
the snapshot date separately.

Public fetches check TLS and public addresses for each redirect. On an OS DNS
error for a supported public domain, the fetcher also tries Google DNS over HTTPS.
In a managed network with an HTTPS proxy, the operator may set
`MYSELFPAY_PROXY_HOSTS` to exact, trusted public provider hostnames separated by
commas. This delegates address resolution for those hosts to that proxy.

Run regression coverage with
`python -m unittest tests.test_scrapper tests.test_scrapper_menus -v`.
Set `MYSELFPAY_TEST_BROWSER=1` to include the Chromium interaction test.
