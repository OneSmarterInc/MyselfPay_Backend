# Previous scans (owner workspace)

Source: CasesByZip_v2 (2).xlsx, Centers sheet only. The values are ZIP-level Medicare scan counts for 2022, 2023 and 2024, not the individual center's scan volume. 2026 nowcasts and modality estimates are excluded. Missing counts remain NULL, distinct from an actual zero. ZIPs retain five characters, including leading zeroes. Shared-ZIP rows repeat geographic totals and must not be summed as distinct center activity.

Prepared CSV: 5,190 center/ZIP rows, 4,952 uniquely mapped center rows. Unmapped names are retained as source records. 941 rows have at least one missing year. The owner-only public aggregate history table shows all imported center/ZIP rows, prioritizing the current center, with search and pagination; staff do not have access.

## Import on AWS

Upload previous-scans-2022-2024.csv into imports/. Then run in the configured backend environment:

```bash
cd /var/www/MyselfPay_Backend
source venv/bin/activate
git pull --ff-only origin main
python manage.py migrate --noinput
python manage.py sync_center_pro
python manage.py import_previous_scans imports/previous-scans-2022-2024.csv --dry-run
python manage.py import_previous_scans imports/previous-scans-2022-2024.csv
sudo systemctl restart myselfpay-backend.service
sudo systemctl status myselfpay-backend.service --no-pager
```

Repeated imports update the same source IDs without duplicating rows. The importer validates all rows before a transaction. This does not change requests, prices or company directories. Deploy frontend main on Vercel after the backend migration/import; no new environment variables.

Prepare future files with `python manage.py prepare_previous_scans workbook.xlsx output.csv`, then review mappings and counts before import. Keep source IDs stable on future updates.
