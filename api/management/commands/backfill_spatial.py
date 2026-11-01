from django.conf import settings
from django.core.management.base import BaseCommand
import json
import core
import spatial


class Command(BaseCommand):
    help = 'Build the spatial projection in bounded batches without modifying source entries.'

    def handle(self, **options):
        path = settings.DOMAIN_DATABASE
        with core.connect(path) as db:
            spatial.initialize(db)
            batches = [row['id'] for row in db.execute('SELECT id FROM batches ORDER BY created')]
        total = 0
        for batch in batches:
            last = ''
            with core.connect(path) as db:
                db.execute('DELETE FROM settings WHERE key=?', ('spatial-ready:' + batch,))
            while True:
                with core.connect(path) as db:
                    rows = db.execute('SELECT entry_key,payload FROM entries WHERE batch_id=? AND entry_key>? ORDER BY entry_key LIMIT 200', (batch, last)).fetchall()
                    for row in rows:
                        total += spatial.index_entry(db, batch, row['entry_key'], json.loads(row['payload']))
                if not rows:
                    break
                last = rows[-1]['entry_key']
            with core.connect(path) as db:
                db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", ('spatial-ready:' + batch, '1'))
        self.stdout.write(self.style.SUCCESS(f'Indexed {total} facility locations across {len(batches)} batches. Source entries preserved.'))
