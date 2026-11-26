# Previous scans: 2025 and 2026 forecasts

The CSV previous-scan-forecasts-2025-2026.csv contains 7,914 unique five-digit ZIPs. Values come directly from the workbook All ZIPs 2025-2026 Forecast sheet: 2024 Total (23-Main), 2025 Est Total (23-Main), 2026 Est Total (23-Main). It does not use the estimated all-payer columns or recalculate projections. Blanks remain null and zero remains zero.

Two nullable fields, scans_2025 and scans_2026, are added to the existing PreviousScanRecord table by migration 0010. The importer validates the full CSV, updates forecast values for all existing rows matching each ZIP, and creates missing zip-total records for saved-ZIP summaries. Existing non-null 2024 and all 2022/2023 counts, center mappings and names are preserved. Missing 2024 values can be filled from the supplied workbook. Reimporting is idempotent. No CHR database URL is required; this uses the existing Django database and same previous-scans table.

```bash
cd /var/www/MyselfPay_Backend
source venv/bin/activate
git pull --ff-only origin main &&
python manage.py migrate --noinput &&
python manage.py import_scan_forecasts /tmp/previous-scan-forecasts-2025-2026.csv --dry-run &&
python manage.py import_scan_forecasts /tmp/previous-scan-forecasts-2025-2026.csv &&
sudo systemctl restart myselfpay-backend.service &&
sudo systemctl status myselfpay-backend.service --no-pager
```

Deploy frontend main on Vercel. Both summary and main table show 2022-2026, with forecasts explicitly labeled for 2025/2026. Eye-icon tooltips support hover, keyboard focus and touch focus. The API remains owner-only; ZIP numeric proximity ordering is unchanged.
