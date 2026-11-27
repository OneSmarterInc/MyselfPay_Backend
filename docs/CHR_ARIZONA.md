# Arizona CHR hospital data

Validated supplied AZ_Hospital_Imaging_Prices_7_Columns.csv: 66,444 source rows, 85 distinct hospital names. Original duplicate rows, missing values, CPT codes, and decimal precision are retained. No source values are manufactured or rewritten.

Uses the existing CHR database selfpay_hospital_compare_v10 and dedicated table public.az_hospital_imaging_prices. Only this Arizona table is replaced atomically. Other states and the main center directory are unchanged. Raw CSV values including CPT code are retained in raw_values. The importer grants SELECT to the existing runtime reader. No Django migration or new runtime environment variable is needed.

Upload the compressed file from Windows PowerShell (replace key path and EC2 public IP):

    scp -i "C:\path\your-key.pem" "C:\Users\gites\Documents\Codex\AZ_Hospital_Imaging_Prices_7_Columns.csv.gz" ubuntu@YOUR_EC2_PUBLIC_IP:/tmp/

Run on AWS, one command at a time:

    cd /var/www/MyselfPay_Backend
    source venv/bin/activate
    git pull --ff-only origin main
    mkdir -p imports
    gzip -dc /tmp/AZ_Hospital_Imaging_Prices_7_Columns.csv.gz > imports/AZ_Hospital_Imaging_Prices_7_Columns.csv
    python manage.py import_chr_arizona imports/AZ_Hospital_Imaging_Prices_7_Columns.csv --dry-run

If your runtime CHR connection is read-only, provide your existing writer connection securely. Paste this line, then enter the writer URL at its prompt:

    read -rsp "CHR writer database URL: " CHR_IMPORT_DATABASE_URL; export CHR_IMPORT_DATABASE_URL; printf '\n'

Then:

    python manage.py import_chr_arizona imports/AZ_Hospital_Imaging_Prices_7_Columns.csv
    unset CHR_IMPORT_DATABASE_URL
    sudo systemctl restart myselfpay-backend.service
    sudo systemctl status myselfpay-backend.service --no-pager

The writer must target selfpay_hospital_compare_v10 and have CREATE, INSERT, DELETE and GRANT privileges. Never put writer credentials into Vercel or frontend variables.

Arizona uses the shared CHR card, four-column hospital ranges, and highest displayed facility price threshold (or $100 if no price). Existing threshold filtering can reduce the number of hospitals displayed below the 85 imported names.

Deploy the latest frontend main commit as well. No manual Vercel deployment is required if main auto-deploys successfully; otherwise manually redeploy production.
