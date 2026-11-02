"""Add one synthetic center by cloning the published directory transactionally."""
import json
import os
import time
import uuid
from datetime import date
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
import booking
import core
from geo import lookup

CENTER_KEY = 'portal-geetesh-pawar-test-center'
CENTER_NAME = 'Geetesh Pawar Test Imaging Center'


class Command(BaseCommand):
    center_key = CENTER_KEY
    center_name = CENTER_NAME
    offer_prefix = 'portal-geetesh-test-'
    include_website = True
    help = 'Preview a clearly labeled synthetic MRI/CT center; --publish adds it while preserving the active directory.'

    def add_arguments(self, parser):
        parser.add_argument('--publish', action='store_true')
        parser.add_argument('--email', default=None)

    def handle(self, **options):
        try:
            email = booking.email_address(options['email'] or os.getenv('TEST_CENTER_EMAIL', ''))
        except booking.BookingError as exc:
            raise CommandError('Set TEST_CENTER_EMAIL to the confirmed center booking recipient.') from exc
        places = [p for p in lookup('Dayton, OH') if p['match'] == 'exact']
        if len(places) != 1:
            raise CommandError('The bundled Dayton geography reference is unavailable.')
        place = places[0]
        center = {
            'name': self.center_name, 'area': 'Dayton', 'states': 'OH',
            'entity': 'Synthetic portal test center', 'category': 'TEST CENTER — not a medical facility',
            'mri': 'MRI test booking workflow only; no medical care is provided.',
            'ct': 'CT test booking workflow only; no medical care is provided.',
            'other': 'Appointment request, email verification and clinic status workflow testing.',
            'coverage': 'Dayton, Ohio — fictional test location. Do not travel to this center.',
            'notes': 'TEST CENTER. Synthetic booking workflow only. This center does not perform exams or provide medical care. Use synthetic patient details.',
            'source': settings.FRONTEND_ORIGIN if self.include_website else '', 'additional': '', 'checked': date.today().isoformat(),
            'email': email, 'email_scope': 'appointments and scheduling', 'appointment_email_approved': True,
            'is_test_center': True, 'location_type': 'site',
            'locations': [{'city': 'Dayton', 'state': 'OH', 'zip': '', 'latitude': place['latitude'], 'longitude': place['longitude'], 'precision': 'city'}],
        }
        center_hash = core.digest(center)
        offers = [{
            'id': self.offer_prefix + mod.lower(), 'entry_name': self.center_name,
            'entry_hash': center_hash, 'mod': mod, 'contrast': 'unknown', 'type': 'quote',
            'region': 'Dayton, OH', 'scope': mod + ' test booking — no clinical exam',
            'included': 'Synthetic appointment workflow only. No clinical service or price is offered.',
            'order': 'Use synthetic patient details. This is not a medical facility.',
            'source': settings.FRONTEND_ORIGIN if self.include_website else '',
        } for mod in ('MRI', 'CT')]
        db = settings.DOMAIN_DATABASE
        if not options['publish']:
            with core.connect(db) as con:
                active = core.active_batch(con)
                count = con.execute('SELECT COUNT(*) AS n FROM entries WHERE batch_id=?', (active,)).fetchone()['n']
            self.stdout.write(f'Preview only: {self.center_name}; Dayton, OH; MRI/CT test services; current listings: {count}. No data changed. Use --publish after reviewing the target database and backup.')
            return
        with core.connect(db) as con:
            con.execute("INSERT INTO settings(key,value) VALUES('portal-test-center-lock','1') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            active = core.active_batch(con)
            existing = con.execute('SELECT content_hash FROM entries WHERE batch_id=? AND entry_key=?', (active, self.center_key)).fetchone()
            if existing and existing['content_hash'] == center_hash:
                self.stdout.write('The test center is already published with these settings. No directory replacement needed.')
                return
            batch = str(uuid.uuid4())
            signature = core.digest({'parent': active, 'test_center': center, 'offers': offers})
            con.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)', (batch, signature, 'Portal synthetic test center', time.time(), 'staged', '{}'))
            if active:
                con.execute('INSERT INTO entries(batch_id,entry_key,content_hash,payload) SELECT ?,entry_key,content_hash,payload FROM entries WHERE batch_id=? AND entry_key<>?', (batch, active, self.center_key))
                con.execute('INSERT INTO offers(batch_id,id,entry_key,amount_cents,max_cents,price_type,payload) SELECT ?,id,entry_key,amount_cents,max_cents,price_type,payload FROM offers WHERE batch_id=? AND entry_key<>?', (batch, active, self.center_key))
            con.execute('INSERT INTO entries VALUES(?,?,?,?)', (batch, self.center_key, center_hash, core.canonical(center)))
            for offer in offers:
                con.execute('INSERT INTO offers VALUES(?,?,?,?,?,?,?)', (batch, offer['id'], self.center_key, None, None, 'quote', core.canonical(offer)))
            count = con.execute('SELECT COUNT(*) AS n FROM entries WHERE batch_id=?', (batch,)).fetchone()['n']
            offer_count = con.execute('SELECT COUNT(*) AS n FROM offers WHERE batch_id=?', (batch,)).fetchone()['n']
            import spatial
            spatial.index_batch(con, batch)
            report = core.canonical({'entry_count': count, 'offer_count': offer_count, 'parent_batch': active, 'test_center_added': True})
            con.execute("UPDATE batches SET status='published',report=? WHERE id=?", (report, batch))
            con.execute("INSERT INTO settings VALUES('active_batch',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (batch,))
            con.execute('INSERT INTO publish_events(batch_id,previous_batch,created,action) VALUES(?,?,?,?)', (batch, active, time.time(), 'publish'))
        self.stdout.write(self.style.SUCCESS(f'Published {self.center_name}. Listings: {count}; offers: {offer_count}. Previous batch preserved: {active}.'))
