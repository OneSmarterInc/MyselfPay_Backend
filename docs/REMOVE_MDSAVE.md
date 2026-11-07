# MDsave directory exclusion

MDsave-linked listings and offers are excluded from all live directory read paths:
full catalog, lightweight metadata and counts, search, H3 bundles, center portal
listings, new booking resolution and mailer contact refresh. The exclusion applies
to stored payloads containing MDsave, MD Save, or MD-save, case-insensitively,
including source URLs and pricing notes. Maryland's state abbreviation MD alone
is not excluded. Independent entries remain if only a separate mapped offer is
linked to MDsave; only the linked offer is withheld in that case.

The remove_mdsave management command defaults to preview. With --publish it clones
the active directory without the linked records, rebuilds spatial locations and
publishes the filtered batch transactionally. It preserves old batches and all
appointment history. Mailer contacts for removed centers are deactivated and their
queued/draft campaign messages are cancelled. Raw import files and historical
request/price snapshots remain as provenance; no clinical or booking data is erased.
Runtime filtering prevents future imports or a historical rollback from exposing
MDsave records again.

Deploy and apply on AWS:

```bash
cd /var/www/MyselfPay_Backend
sudo systemctl stop myselfpay-mailer.service
sudo git pull --ff-only origin codex/mobile-username-accounts
sudo ./venv/bin/python manage.py migrate --noinput
sudo ./venv/bin/python manage.py remove_mdsave --publish
sudo ./venv/bin/python manage.py sync_mailer_contacts
sudo ./venv/bin/python manage.py check
sudo systemctl restart myselfpay-backend.service
sudo systemctl start myselfpay-mailer.service
```

Run remove_mdsave without --publish to see affected counts without changing data.
Refresh the website after publication to discard previously loaded result cards.
No frontend/Vercel deployment or new database migration is needed for this policy.
If the optional mailer service is not installed, omit its stop/start commands.
