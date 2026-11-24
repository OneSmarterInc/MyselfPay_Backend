# Employee to near you

The center workspace includes the workbook company directory for both owner and staff roles. The API always checks current membership for the selected center. Public company data is shown with all ten original columns, searchable and paginated, nearest first. Source verification and employee-count text are preserved as supplied, not independently asserted. Distances over 100 miles are excluded; unknown distance text is retained and labeled.

## Prepared files

The supplied workbook has 156,076 company rows under 3,484 operator blocks. The prepared UTF-8 CSV maps 2,442 operator blocks uniquely by normalized center name (ZIP disambiguates duplicate names). The remaining 1,042 groups are retained unmapped. Data is never assigned by loose/fuzzy name guessing. The source workbook and company CSV are data uploads, not application code or Git commits.

CSV: source_id, operator_name, operator_address, operator_location, center_key, source_row, company_name, company_address, public_email, email_type, business_phone, employee_count, distance_miles, location_distance_status, verification_status, source_links.

source_id identifies the operator block; source_row retains the Excel row. A repeated import replaces records only for included operator groups and does not duplicate them. All included changes are transactional. Do not renumber source IDs in an existing dataset. A structurally changed source workbook should be reviewed before replacement.

## AWS deployment

Back up the existing PostgreSQL database with the normal production backup process. Pull backend main, then run in the same configured environment as the backend service:

```bash
cd /var/www/MyselfPay_Backend
source venv/bin/activate
git pull --ff-only origin main
python manage.py migrate --noinput
python manage.py sync_center_pro
mkdir -p imports
# Upload center-nearby-companies.csv.gz into imports/ before continuing.
gzip -dk imports/center-nearby-companies.csv.gz
python manage.py import_employer_directory imports/center-nearby-companies.csv --dry-run
python manage.py import_employer_directory imports/center-nearby-companies.csv
sudo systemctl restart myselfpay-backend.service
sudo systemctl status myselfpay-backend.service --no-pager
```

Deploy frontend main manually on Vercel after the migration/import. No additional environment variable is required.

## Mapping review

Use center-company-mapping-review.csv to review each operator and its center_key. Fill blank keys only after confirming the correct center. Existing keys come from the stable active entries.entry_key / Center.directory_key, not the center's numeric database ID. Reimport with the reviewed mapping:

```bash
python manage.py import_employer_directory imports/center-nearby-companies.csv --mapping-file imports/center-company-mapping-review.csv --dry-run
python manage.py import_employer_directory imports/center-nearby-companies.csv --mapping-file imports/center-company-mapping-review.csv
```

Invalid center keys stop the import. Blank mapping keys leave the supplied mapping unchanged; initially blank operators remain unavailable until mapped. Existing assignments, booking requests and prices are not modified.

To prepare a replacement from the original operator-wise workbook, run `python manage.py prepare_employer_directory /path/to/workbook.xlsx /path/to/output.csv`; inspect mappings before import. The converter uses standard-library streaming XML and needs no Excel server dependency.
