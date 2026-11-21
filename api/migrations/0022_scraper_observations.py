from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("api", "0021_admin_totp")]
    operations = [migrations.RunSQL("""CREATE TABLE IF NOT EXISTS scraper_observations (
        observation_id TEXT PRIMARY KEY, active_batch_id TEXT NOT NULL,
        entry_key TEXT, source_url TEXT NOT NULL, checked_at TEXT NOT NULL,
        record_kind TEXT NOT NULL, exam TEXT, amount_cents INTEGER,
        price_type TEXT, evidence TEXT NOT NULL, source_record TEXT NOT NULL,
        FOREIGN KEY(active_batch_id, entry_key) REFERENCES entries(batch_id, entry_key)
    )""")]
