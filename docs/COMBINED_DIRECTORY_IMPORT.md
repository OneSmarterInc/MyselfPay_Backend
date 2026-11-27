## Workbook-only publication (current requirement)

Use --replace-existing for both preview and import. This publishes only the 5,643 importable records in the supplied CSV, unlists all older unmatched records, and does not carry over old mapped price offers. Matching booking/center IDs remain stable. Previous batches, accounts and bookings are preserved. Run sync_center_pro afterward and restart the backend. The CSV does not need to be uploaded again if imports/combined-imaging-directory.csv is already present. No Vercel deployment is required for this backend-only change.

    python manage.py import_combined_directory imports/combined-imaging-directory.csv --replace-existing --dry-run
    python manage.py import_combined_directory imports/combined-imaging-directory.csv --replace-existing
    python manage.py sync_center_pro
    sudo systemctl restart myselfpay-backend.service

The older upsert mode described below remains available without --replace-existing, but does not meet the workbook-only requirement.

# Combined imaging directory import

Use the MAIN directory database configured by Django DOMAIN_DATABASE, not the CHR hospital database. No new database URL or migration is needed.

The supplied workbook contains 5,688 source records. The prepared CSV retains every source row and original field in source_json. Forty-four archived promotions and one unnamed record have import_action=skip with a reason. There are 5,643 upsert rows. Against the local active directory the dry run matched 5,006 centers, added 637, and preserved one unmatched center. Production counts depend on its current active batch.

Identity matches by source_record_id (including known aliases), preserving entry keys used by bookings and center access. An unambiguous exact name/state/ZIP fallback is available. Same-name branches with different ZIPs or stable source IDs remain separate. Replacements use the workbook values, including blanks. Unmatched existing entries are retained. Old mapped offers for changed entries are withheld so they cannot override the workbook; owner-managed offers and bookings are not deleted. Original prices and all source evidence remain in the database. Cards show the source price summary and original verification status; repeated supporting findings are stored separately from the public catalog to avoid a huge response. Missing ZIPs are never invented.

States are retained in states and state_1 through state_16. locations_json pairs ZIPs with Census states; independently ordered state and ZIP lists are never zipped together. Multi-state coverage remains searchable in every specified state, even where a ZIP has no supported coordinates. A grouped network has no arbitrary single primary ZIP. Leading zero ZIPs remain strings.

The import validates all CSV rows before changing data, publishes entries and rebuilt spatial indexes in one transaction, and retains the complete previous batch. Repeating the same import is a no-op. A failure rolls back publication. The command prints parent_batch and the new batch ID. Keep this report.

Preparation (already performed for the downloadable CSV):

    python manage.py prepare_combined_directory WORKBOOK.xlsx combined-imaging-directory.csv

AWS import:

    cd /var/www/MyselfPay_Backend
    source venv/bin/activate
    git pull --ff-only origin main
    mkdir -p imports
    gzip -dc /tmp/combined-imaging-directory.csv.gz > imports/combined-imaging-directory.csv
    python manage.py check
    python manage.py import_combined_directory imports/combined-imaging-directory.csv --dry-run
    python manage.py import_combined_directory imports/combined-imaging-directory.csv
    python manage.py sync_center_pro
    sudo systemctl restart myselfpay-backend.service
    sudo systemctl status myselfpay-backend.service --no-pager

Rollback if needed: replace PREVIOUS_BATCH_ID with parent_batch printed by the import, then run:

    python manage.py shell -c "from django.conf import settings; import core; print(core.publish('PREVIOUS_BATCH_ID',settings.DOMAIN_DATABASE))"
    python manage.py sync_center_pro
    sudo systemctl restart myselfpay-backend.service

The frontend main commit is also required for complete workbook prices and unverified price labels. Email addresses stay in the database and CSV and are not displayed on public cards. If Vercel automatically deploys main, wait for that successful deployment; otherwise manually redeploy the frontend production project. Importing the CSV alone does not redeploy Vercel.
