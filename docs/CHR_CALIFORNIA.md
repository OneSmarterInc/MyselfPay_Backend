# California hospital source import

The original CA_Hospital_Imaging_Prices_7_Columns.csv has six actual columns: State, Hospital, Distance, Cash price, Gross charge, Insurer rates. The supplied file has 99,054 rows and 79 hospital names. It has no exam types, ZIPs or coordinates; all distances are blank. Therefore California is presented as paginated statewide source rows, separately from Ohio exam-specific threshold/distance comparisons. The data does not support assigning a row to MRI or CT or calculating a center distance.

## Storage

`public.ca_hospital_imaging_prices` is created inside `selfpay_hospital_compare_v10`, the existing CHR database. This is independent of the Django application database migrations. Raw CSV values, row numbers and a file SHA-256 are retained. Numeric columns use unconstrained NUMERIC to preserve prices with three decimals. Missing values are NULL; repeated rows are preserved because the source has no exam identifiers.

The importer validates the whole file before opening a write connection, acquires a transaction advisory lock, copies rows with PostgreSQL COPY, and replaces only this dedicated CA table atomically. Repeat imports do not accumulate copies. Other CHR tables are unchanged. The runtime role receives SELECT only on the new table. On error the table replacement rolls back.

## Upload (Windows PowerShell)

Replace the server and PEM paths with your existing SSH details:

```powershell
$AwsServer = "YOUR_EC2_PUBLIC_IP"
$SshKey = "C:\path\your-key.pem"
scp -i "$SshKey" "C:\Users\gites\Downloads\CA_Hospital_Imaging_Prices_7_Columns.csv" "ubuntu@${AwsServer}:/tmp/CA_Hospital_Imaging_Prices_7_Columns.csv"
```

## AWS

```bash
cd /var/www/MyselfPay_Backend
source venv/bin/activate
git pull --ff-only origin main &&
python manage.py import_chr_california /tmp/CA_Hospital_Imaging_Prices_7_Columns.csv --dry-run
```

The runtime CHR_DATABASE_URL remains unchanged. For import only, enter a CHR connection URL with CREATE, INSERT, DELETE and GRANT rights. Keep it private and use the selfpay_hospital_compare_v10 database. It is not a frontend variable or a value to commit.

Run this line and enter the write connection URL at the hidden prompt:

```bash
read -rsp "CHR database write URL: " CHR_IMPORT_DATABASE_URL; export CHR_IMPORT_DATABASE_URL; printf '\n'
```

Then:

```bash
python manage.py import_chr_california /tmp/CA_Hospital_Imaging_Prices_7_Columns.csv &&
python manage.py shell -c "from chr_california import prices; d=prices({}); print('Rows:', d['count'], 'Hospitals:', d['hospital_count'])" &&
sudo systemctl restart myselfpay-backend.service &&
sudo systemctl status myselfpay-backend.service --no-pager
unset CHR_IMPORT_DATABASE_URL
```

Expected: Rows: 99054 Hospitals: 79. No Django migration or new worker is required. Deploy latest frontend main on Vercel after import (manual deployment only if automatic Git deployment is disabled).

The public CHR view and owner comparison view both route California listings to the new table through their existing authenticated endpoint, with state=CA, server-side hospital search, and page sizes 10/20/50. Only a page of rows is returned. Other unsupported states retain the existing unavailable message.

Validation: original-file dry run, importer/precision/null/duplicate and bounded-query tests, existing CHR and center tests, frontend tests/build, desktop/mobile search and pagination checks. Tests do not import into production.

## Shared CHR chart layout

The California UI uses the shared CHR dialog, range chart, facility marker, modality reference buttons and hospital table. The Source column is omitted as requested. `view=hospitals` returns one row per hospital with cash, gross and insurer min/max values aggregated from the existing table. MRI/CT buttons select only the center's saved reference price: the imported hospital rows have no modality labels, so the UI does not claim MRI/CT-specific counts or quotes. Distances remain unavailable. The default raw paginated API remains available.

No schema change or CSV re-import is required for this display update. Pull backend main and restart the service, then deploy frontend main. Hospital summary SQL was checked against PostgreSQL using synthetic VALUES in a read-only transaction; no production data was modified during testing.
