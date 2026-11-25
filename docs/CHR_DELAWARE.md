# Delaware CHR hospital prices

Import `Delaware_Hospital_Imaging_Prices.csv` into the existing CHR database `selfpay_hospital_compare_v10` with:

```bash
python manage.py import_chr_delaware /tmp/Delaware_Hospital_Imaging_Prices.csv --dry-run
# Set CHR_IMPORT_DATABASE_URL privately to a writer/admin connection for this database.
python manage.py import_chr_delaware /tmp/Delaware_Hospital_Imaging_Prices.csv
sudo systemctl restart myselfpay-backend.service
sudo systemctl status myselfpay-backend.service --no-pager
```

The importer creates and atomically replaces only `public.de_hospital_imaging_prices`. No Django migration is required. It preserves 3,208 source rows, 13 priced hospital identities, all modality/contrast records, original metadata and missing values. Source notes include hospitals whose shared-file campus applicability is unverified. Six additional hospitals in the workbook status sheet have no accepted price records and are not fabricated as prices.

Use a writer connection with CREATE, INSERT, DELETE and GRANT permission. The importer grants SELECT to the configured runtime CHR database user. Do not change the runtime read connection or expose credentials in frontend environment variables. Unset the temporary writer variable after importing.

The CHR API dispatches state DE/Delaware to this table. The display uses the existing chart and MRI/CT tabs without a Source column. Comparison rows are noncontrast, outpatient/both setting, facility/hospital billed, and not explicitly professional billing. It groups by source Place ID and modality; filters within 50 approximate miles and above the center price. Thus visible counts may be lower than 13.

Distances use bundled Census ZIP-area reference coordinates, not hospital building locations. A saved Delaware ZIP is preferred; saved coordinates or an exact Delaware city provide a clearly labeled fallback. CPT/contrast and source metadata remain in raw_values for traceability. Confirm campus applicability before relying on a shared health-system file.

Deploy the updated frontend main build on Vercel as well as updating/restarting AWS. Git automatic deployments suffice when enabled; otherwise redeploy main manually. California and Ohio tables are unchanged.
