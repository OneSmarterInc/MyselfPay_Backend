# Tennessee CHR prices

Use the original TN_Hospital_Imaging_Prices_7_Columns_Updated.csv without removing CPT code.

```bash
python manage.py import_chr_tennessee /tmp/TN_Hospital_Imaging_Prices_7_Columns_Updated.csv --dry-run
# Privately set CHR_IMPORT_DATABASE_URL to a writer connection for selfpay_hospital_compare_v10.
python manage.py import_chr_tennessee /tmp/TN_Hospital_Imaging_Prices_7_Columns_Updated.csv
sudo systemctl restart myselfpay-backend.service
sudo systemctl status myselfpay-backend.service --no-pager
unset CHR_IMPORT_DATABASE_URL
```

The importer creates and atomically replaces only public.tn_hospital_imaging_prices, grants runtime SELECT, and preserves the original seven columns in raw_values, including CPT code. The supplied file has 130,359 records and 85 hospital names. No Django migration is required. Existing states are unchanged.

Deploy frontend main on Vercel (automatic Git deployment or manual redeployment). Tennessee center CHR cards request state=TN&view=hospitals, showing statewide hospital ranges without Source. Distances were not supplied; the chart combines source exams and does not claim an exact MRI/CT quote.

Tennessee uses shared MRI, then CT, then $100 threshold screening. All three qualifying lower bounds are required; original upper bounds are unchanged. Distance is omitted from the display.
