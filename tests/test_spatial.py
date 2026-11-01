import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ.setdefault('DJANGO_DEBUG', 'true')
import django
django.setup()
from django.test import override_settings
from django.core.management import call_command
from rest_framework.test import APIClient
import h3
import accounts
import core
import spatial


class SpatialTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.getenv('SPATIAL_TEST_DATABASE_URL') or str(Path(self.temp.name) / 'spatial.db')
        self.settings = override_settings(DOMAIN_DATABASE=self.path, DEBUG=True, FRONTEND_ORIGIN='http://localhost:5173', ALLOWED_HOSTS=['testserver'])
        self.settings.enable()
        core.initialize(self.path)
        accounts.initialize(self.path)
        self.center = h3.latlng_to_cell(39.1, -84.5, 7)
        self.disk = sorted(h3.grid_disk(self.center, 1))
        with core.connect(self.path) as db:
            for batch in ('active', 'old'):
                db.execute('DELETE FROM offers WHERE batch_id=?', (batch,))
                db.execute('DELETE FROM entries WHERE batch_id=?', (batch,))
                db.execute('DELETE FROM batches WHERE id=?', (batch,))
            db.execute("DELETE FROM settings WHERE key='active_batch'")
            for batch in ('active', 'old'):
                db.execute('INSERT INTO batches VALUES(?,?,?,?,?,?)', (batch, batch, 'synthetic', 0, 'published', '{}'))
            db.execute("INSERT INTO settings VALUES('active_batch','active')")
            for index, cell in enumerate([*self.disk, h3.latlng_to_cell(40, -85, 7)]):
                lat, lng = h3.cell_to_latlng(cell)
                entry = {'name': f'Synthetic center {index}', 'area': 'Synthetic', 'location_type': 'site', 'locations': [{'city': 'Synthetic', 'state': 'OH', 'latitude': lat, 'longitude': lng, 'precision': 'address'}], 'email': 'private@example.test', 'appointment_email_approved': True, 'mri': '$350 without contrast'}
                for batch in ('active', 'old'):
                    db.execute('INSERT INTO entries VALUES(?,?,?,?)', (batch, str(index), 'preserved-hash', json.dumps(entry)))
            spatial.index_batch(db, 'active')
            spatial.index_batch(db, 'old')
        self.client = APIClient()

    def tearDown(self):
        self.settings.disable()
        self.temp.cleanup()

    def guest(self):
        response = self.client.post('/api/guest', {}, format='json', HTTP_ORIGIN='http://localhost:5173')
        self.assertEqual(response.status_code, 200)

    def test_bundle_neighbors_active_batch_and_safe_metadata(self):
        self.guest()
        with patch('h3.latlng_to_cell', side_effect=AssertionError('Read-time H3 conversion')):
            response = self.client.get(f'/api/v1/spatial-bundle/{self.center}/')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['version'], 'active')
        self.assertEqual(set(data['queried_cells']), set(self.disk))
        self.assertEqual(data['count'], len(self.disk))
        self.assertFalse(data['truncated'])
        self.assertEqual(len({r['id'] for r in data['records']}), len(self.disk))
        for record in data['records']:
            self.assertEqual(h3.get_resolution(record['h3_res11']), 11)
            self.assertNotIn('email', record['entry'])
            self.assertNotIn('appointment_email_approved', record['entry'])
            self.assertEqual(record['location']['precision'], 'address')
        self.assertEqual(response['Cache-Control'], 'private, no-store')
        self.assertIn('Cookie', response['Vary'])

    def test_bundle_without_trailing_slash_matches_canonical_response(self):
        self.guest()
        canonical = self.client.get(f'/api/v1/spatial-bundle/{self.center}/')
        proxied = self.client.get(f'/api/v1/spatial-bundle/{self.center}')
        self.assertEqual(proxied.status_code, 200)
        self.assertEqual(proxied.json(), canonical.json())
        self.assertEqual(proxied['Cache-Control'], 'private, no-store')

    def test_authentication_validation_and_no_coordinate_parameters(self):
        self.assertEqual(self.client.get(f'/api/v1/spatial-bundle/{self.center}/').status_code, 401)
        self.guest()
        for cell in ('not-h3', h3.latlng_to_cell(39.1, -84.5, 11), self.center.upper(), '0' * 15):
            self.assertEqual(self.client.get(f'/api/v1/spatial-bundle/{cell}/').status_code, 400)
        self.assertEqual(self.client.get(f'/api/v1/spatial-bundle/{self.center}/?latitude=39').status_code, 400)

    def test_indexed_lookup_and_idempotent_backfill_preserve_sources(self):
        with core.connect(self.path) as db:
            before = [dict(row) for row in db.execute('SELECT * FROM entries ORDER BY batch_id,entry_key')]
            postgres = self.path.startswith(('postgres://', 'postgresql://'))
            if postgres:
                db.execute('SET LOCAL enable_seqscan=off')
            explain = 'EXPLAIN (COSTS OFF)' if postgres else 'EXPLAIN QUERY PLAN'
            plan = db.execute(explain + ' SELECT id FROM spatial_locations WHERE batch_id=? AND h3_res7 IN (?)', ('active', self.center)).fetchall()
            self.assertIn('spatial_batch_res7', str([dict(row) for row in plan]))
        from io import StringIO
        call_command('backfill_spatial', stdout=StringIO())
        call_command('backfill_spatial', stdout=StringIO())
        with core.connect(self.path) as db:
            self.assertEqual(before, [dict(row) for row in db.execute('SELECT * FROM entries ORDER BY batch_id,entry_key')])
            self.assertEqual(db.execute("SELECT COUNT(*) AS n FROM spatial_locations WHERE batch_id IN ('active','old')").fetchone()['n'], (len(self.disk) + 1) * 2)

    def test_not_ready_and_payload_limit(self):
        self.guest()
        with core.connect(self.path) as db:
            db.execute("DELETE FROM settings WHERE key='spatial-ready:active'")
        response = self.client.get(f'/api/v1/spatial-bundle/{self.center}/')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response['Retry-After'], '60')
        with core.connect(self.path) as db:
            spatial.index_batch(db, 'active')
        with patch('spatial.BUNDLE_LIMIT', 2):
            data = spatial.bundle(self.path, self.center)
        self.assertEqual(len(data['records']), 2)
        self.assertEqual(data['count'], len(self.disk))
        self.assertTrue(data['truncated'])

    def test_pentagons_and_invalid_facility_locations(self):
        pentagon = h3.get_pentagons(7)[0]
        self.assertEqual(set(spatial.bundle(self.path, pentagon)['queried_cells']), set(h3.grid_disk(pentagon, 1)))
        for latitude in (True, float('nan'), 91):
            self.assertIsNone(spatial.prepare_location({'latitude': latitude, 'longitude': 0, 'precision': 'city'}))

    def test_registration_zip_is_optional_validated_and_saved_across_login(self):
        credentials = {'name': 'Synthetic ZIP User', 'email': f'zip-{uuid.uuid4().hex}@example.com', 'password': 'Synthetic-only-passphrase!', 'zip_code': '45202-1234'}
        for bad in ('45202', '452021234', '45202-123', 'ABCDE-1234'):
            response = self.client.post('/api/register', {**credentials, 'zip_code': bad}, format='json', HTTP_ORIGIN='http://localhost:5173')
            self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/register', credentials, format='json', HTTP_ORIGIN='http://localhost:5173')
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['member']['zip_code'], '45202-1234')
        self.assertEqual(self.client.get('/api/me').json()['member']['zip_code'], '45202-1234')
        fresh = APIClient()
        response = fresh.post('/api/login', credentials, format='json', HTTP_ORIGIN='http://localhost:5173')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['member']['zip_code'], '45202-1234')
