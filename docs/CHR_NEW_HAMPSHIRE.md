# New Hampshire CHR prices

Use the original NH_Hospital_Imaging_Prices_7_Columns.csv without removing CPT code.

```bash
python manage.py import_chr_new_hampshire /tmp/NH_Hospital_Imaging_Prices_7_Columns.csv --dry-run
# Privately set CHR_IMPORT_DATABASE_URL to a writer connection for selfpay_hospital_compare_v10.
python manage.py import_chr_new_hampshire /tmp/NH_Hospital_Imaging_Prices_7_Columns.csv
sudo systemctl restart myselfpay-backend.service
sudo systemctl status myselfpay-backend.service --no-pager
unset CHR_IMPORT_DATABASE_URL
```

The importer creates and atomically replaces only public.nh_hospital_imaging_prices, grants runtime SELECT, and preserves the original seven columns in raw_values, including CPT code. The supplied file has 50,624 records and 26 hospital names. No Django migration is required. Existing states are unchanged.

Deploy frontend main on Vercel (automatic Git deployment or manual redeployment). New Hampshire center CHR cards request state=NH&view=hospitals, showing statewide hospital ranges without Source. Distances were not supplied; the chart combines source exams and does not claim an exact MRI/CT quote.

New Hampshire uses shared MRI, then CT, then $100 threshold screening. All three qualifying lower bounds are required; original upper bounds are unchanged. Distance is omitted from the display.
