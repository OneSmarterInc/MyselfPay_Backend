# Hide MDsave branding, keep centers and prices

The earlier listing exclusion has been revoked. Marketplace-linked centers and
offers remain visible and bookable. The frontend replaces the MDsave brand name
with the generic word marketplace in display text; it preserves prices, prepaid
conditions, booking IDs and original source/website URLs. No provider links are
rewritten to incorrect URLs. Existing appointment records remain unchanged.

If the previous removal command was run, restore the missing records by merging
from its preserved parent batch. The restore command keeps current records and
newly added centers, never overwrites a newer existing center, and only restores
mapped offers if their entry's content hash still matches the old source. It is
idempotent and does not reactivate cancelled or suppressed email campaigns.
The old remove_mdsave command now refuses to run.

```bash
cd /var/www/MyselfPay_Backend
sudo systemctl stop myselfpay-mailer.service
sudo git pull --ff-only origin codex/mobile-username-accounts
sudo ./venv/bin/python manage.py restore_mdsave --publish
sudo ./venv/bin/python manage.py sync_mailer_contacts
sudo ./venv/bin/python manage.py check
sudo systemctl restart myselfpay-backend.service
sudo systemctl start myselfpay-mailer.service
```

If no earlier removal happened, restore_mdsave reports that no restoration is
needed; removing the backend exclusion is sufficient. No migration is needed.
Deploy the paired frontend change and refresh the website.
