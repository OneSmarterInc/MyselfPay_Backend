"""Manage the explicitly labeled Cincinnati demo without resetting live data."""
import time
import uuid
from django.conf import settings
from django.core.management.base import CommandError
import core
from .add_test_center import Command as TestCenterCommand


class Command(TestCenterCommand):
    help = 'Preview ammar test in Cincinnati; --publish adds it, --remove unlists it.'
    center_key = 'portal-ammar-test-center'
    center_name = 'ammar test'
    offer_prefix = 'portal-ammar-test-'
    include_website = False
    city = 'Cincinnati'

    def add_arguments(self, parser):
        super().add_arguments(parser)
        parser.add_argument('--remove', action='store_true')

    def handle(self, **options):
        if not options['remove']:
            return super().handle(**options)
        if options['publish']:
            raise CommandError('Choose --publish or --remove, not both.')
        with core.connect(settings.DOMAIN_DATABASE) as con:
            con.execute("INSERT INTO settings(key,value) VALUES('portal-test-center-lock','1') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            active = core.active_batch(con)
            if not con.execute('SELECT 1 FROM entries WHERE batch_id=? AND entry_key=?', (active, self.center_key)).fetchone():
                self.stdout.write('ammar test is already absent. No change.')
                return
            batch = str(uuid.uuid4())
            signature = core.digest({'parent': active, 'remove': self.center_key})
            con.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)', (batch, signature, 'Unlist ammar demo center', time.time(), 'staged', '{}'))
            con.execute('INSERT INTO entries(batch_id,entry_key,content_hash,payload) SELECT ?,entry_key,content_hash,payload FROM entries WHERE batch_id=? AND entry_key<>?', (batch, active, self.center_key))
            con.execute('INSERT INTO offers(batch_id,id,entry_key,amount_cents,max_cents,price_type,payload) SELECT ?,id,entry_key,amount_cents,max_cents,price_type,payload FROM offers WHERE batch_id=? AND entry_key<>?', (batch, active, self.center_key))
            import spatial
            spatial.index_batch(con, batch)
            report = core.canonical({'parent_batch': active, 'removed_test_center': self.center_key})
            con.execute("UPDATE batches SET status='published',report=? WHERE id=?", (report, batch))
            con.execute("INSERT INTO settings VALUES('active_batch',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (batch,))
            con.execute('INSERT INTO publish_events(batch_id,previous_batch,created,action) VALUES(?,?,?,?)', (batch, active, time.time(), 'publish'))
        self.stdout.write('ammar test unlisted. Other listings, previous batches and booking records preserved.')
