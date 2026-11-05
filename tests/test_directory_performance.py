import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
import core
import directory_cache


class DirectoryReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = os.getenv('API_TEST_DATABASE_URL') or str(Path(self.temp.name) / 'directory.sqlite3')
        directory_cache.clear()
        core.initialize(self.path)
        self.batch = 'perf-' + uuid.uuid4().hex
        with core.connect(self.path) as db:
            self.previous = core.active_batch(db)
            db.execute('INSERT INTO batches VALUES(?,?,?,0,?,?)', (self.batch, self.batch, 'synthetic', 'published', '{}'))
            db.execute("INSERT INTO settings VALUES('active_batch',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (self.batch,))
            entry = {'name': 'Synthetic', 'states': 'OH; KY', 'area': 'Cincinnati', 'mri': '$350', 'notes': 'not metadata ' * 500,
                     'location_type': 'facility', 'locations': [{'city': 'Cincinnati', 'state': 'OH'}]}
            legacy = {'name': 'Legacy', 'states': 'KY', 'area': 'Louisville'}
            for key, value in [('center', entry), ('legacy', legacy)]:
                db.execute('INSERT INTO entries VALUES(?,?,?,?)', (self.batch, key, key, json.dumps(value)))
            offer = {'id': 'offer', 'mod': 'MRI', 'type': 'fixed'}
            db.execute('INSERT INTO offers VALUES(?,?,?,?,?,?,?)', (self.batch, 'offer', 'center', 35000, None, 'fixed', json.dumps(offer)))

    def tearDown(self):
        with core.connect(self.path) as db:
            if self.previous:
                db.execute("UPDATE settings SET value=? WHERE key='active_batch'", (self.previous,))
            else:
                db.execute("DELETE FROM settings WHERE key='active_batch'")
            db.execute('DELETE FROM offers WHERE batch_id=?', (self.batch,))
            db.execute('DELETE FROM entries WHERE batch_id=?', (self.batch,))
            db.execute('DELETE FROM settings WHERE key=?', ('spatial-ready:' + self.batch,))
            db.execute('DELETE FROM batches WHERE id=?', (self.batch,))
        directory_cache.clear()
        self.temp.cleanup()

    def test_metadata_matches_full_catalog_and_readiness_is_current(self):
        full = core.catalog(self.path)
        metadata = core.catalog_metadata(self.path)
        self.assertEqual(metadata['locations'], full['locations'])
        self.assertEqual((metadata['entry_count'], metadata['offer_count'], metadata['state_count']), (2, 1, 2))
        self.assertNotIn('not metadata', json.dumps(metadata))
        with core.connect(self.path) as db:
            db.execute('INSERT INTO settings VALUES(?,?)', ('spatial-ready:' + self.batch, 'yes'))
        self.assertTrue(core.catalog_metadata(self.path)['spatial_ready'])
        self.assertEqual(core.directory_summary(self.path), {'listing_count': 2, 'state_count': 2})

    def test_search_annotations_cannot_poison_cached_catalog(self):
        first = core.catalog(self.path)
        first['entries'][0]['name'] = 'mutated'
        first['offers'][0]['budget_status'] = 'private to this search'
        second = core.catalog(self.path)
        self.assertNotIn('mutated', [entry['name'] for entry in second['entries']])
        self.assertNotIn('budget_status', second['offers'][0])

    def test_warm_catalog_does_not_reread_payloads_and_batch_change_invalidates(self):
        core.catalog(self.path)
        original = core.connect
        queries = []
        if not str(self.path).startswith(('postgres://', 'postgresql://')):
            def connect(path):
                db = original(path)
                db.set_trace_callback(queries.append)
                return db
            with patch('core.connect', side_effect=connect):
                core.catalog(self.path)
            self.assertFalse(any('FROM entries' in query or 'FROM offers' in query for query in queries), queries)
        with core.connect(self.path) as db:
            db.execute("UPDATE settings SET value='missing-version' WHERE key='active_batch'")
        self.assertEqual(core.catalog(self.path)['entries'], [])

    def test_cache_coalesces_concurrent_loads_expires_and_isolates_databases(self):
        calls = []
        def load():
            calls.append(1)
            return {'entries': []}
        with ThreadPoolExecutor(max_workers=8) as workers:
            values = list(workers.map(lambda _: directory_cache.snapshot('catalog', 'db1', 'v1', load), range(16)))
        self.assertEqual(len(calls), 1)
        self.assertIsNot(values[0], values[1])
        directory_cache.snapshot('catalog', 'db2', 'v1', load)
        self.assertEqual(len(calls), 2)
        with patch('directory_cache.time.monotonic', return_value=float('inf')):
            directory_cache.snapshot('catalog', 'db1', 'v1', load)
        self.assertEqual(len(calls), 3)


@unittest.skipUnless(os.getenv('API_TEST_DATABASE_URL'), 'Requires isolated CI PostgreSQL')
class PostgresPoolTests(unittest.TestCase):
    def test_reuses_connection_and_rolls_back_before_returning_it(self):
        from postgres_store import PostgresConnection
        parts = urlsplit(os.environ['API_TEST_DATABASE_URL'])
        query = list(parse_qsl(parts.query)) + [('application_name', 'selfpay-pool-test-' + uuid.uuid4().hex)]
        url = urlunsplit(parts._replace(query=urlencode(query)))
        with PostgresConnection(url) as db:
            first = db.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid']
            db.execute('CREATE TEMP TABLE pool_rollback_test(value INTEGER)')
        with self.assertRaises(RuntimeError):
            with PostgresConnection(url) as db:
                db.execute('INSERT INTO pool_rollback_test VALUES(1)')
                raise RuntimeError('synthetic rollback')
        with PostgresConnection(url) as db:
            self.assertEqual(db.execute('SELECT pg_backend_pid() AS pid').fetchone()['pid'], first)
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM pool_rollback_test').fetchone()['n'], 0)


class DirectoryCompressionTests(unittest.TestCase):
    def setUp(self):
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        os.environ.setdefault('DJANGO_DEBUG', 'true')
        import django
        django.setup()

    def test_compresses_directory_but_not_booking_or_account_responses(self):
        import gzip
        from django.http import HttpResponse
        from django.test import RequestFactory
        from api.middleware import DirectoryCompressionMiddleware
        body = (b'public-directory-data-' * 500)
        middleware = DirectoryCompressionMiddleware(lambda request: None)
        for path, compressed in [('/api/search', True), ('/api/catalog', True), ('/api/booking/patient', False), ('/api/me', False), ('/admin/', False)]:
            request = RequestFactory().get(path, HTTP_ACCEPT_ENCODING='gzip')
            response = HttpResponse(body, content_type='application/json')
            response['Cache-Control'] = 'private, no-store'
            result = middleware.process_response(request, response)
            self.assertEqual(result.get('Content-Encoding') == 'gzip', compressed)
            self.assertEqual(result['Cache-Control'], 'private, no-store')
            self.assertEqual(gzip.decompress(result.content) if compressed else result.content, body)
