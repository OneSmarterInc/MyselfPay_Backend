"""Real Django HTTP flows against an isolated file database or CI PostgreSQL."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
os.environ.setdefault('DJANGO_DEBUG', 'true')
import django
django.setup()
from django.test import override_settings
from rest_framework.test import APIClient as Client
from cryptography.fernet import Fernet
import accounts
import booking
import core

class APITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = os.environ.get('API_TEST_DATABASE_URL') or str(Path(self.temp.name) / 'api.db')
        self.settings = override_settings(DOMAIN_DATABASE=self.db, FRONTEND_ORIGIN='http://localhost:5173', DEBUG=True, ALLOWED_HOSTS=['testserver'])
        self.settings.enable()
        self.env = patch.dict(os.environ, {'BOOKING_ENCRYPTION_KEY': Fernet.generate_key().decode()})
        self.env.start()
        core.initialize(self.db)
        accounts.initialize(self.db)
        with core.connect(self.db) as con:
            con.executescript((Path(core.__file__).parent / 'ops/booking-schema.sql').read_text())
            # CI database is disposable and must never point at production.
            for table in ['booking_email_checks', 'booking_mail_limits', 'booking_requests', 'corrections', 'preferences', 'conversations', 'guest_usage', 'sessions', 'user_accounts', 'members', 'login_limits']:
                con.execute('DELETE FROM ' + table)
        self.client = Client(enforce_csrf_checks=True)
        self.csrf = ''

    def tearDown(self):
        self.env.stop()
        self.settings.disable()
        self.temp.cleanup()

    def post(self, route, data=None, client=None, csrf=None, origin='http://localhost:5173'):
        response = (client or self.client).post('/api/' + route, json.dumps(data or {}), content_type='application/json', HTTP_ORIGIN=origin, HTTP_X_CSRF_TOKEN=self.csrf if csrf is None else csrf)
        value = response.json()
        if value.get('csrf') and (client is None or client is self.client):
            self.csrf = value['csrf']
        return response

    def guest(self):
        response = self.post('guest')
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_guest_search_csrf_cookie_and_logout(self):
        self.assertEqual(self.client.get('/api/me').status_code, 401)
        self.assertEqual(self.post('guest', origin='https://evil.example').status_code, 403)
        self.guest()
        self.assertTrue(self.client.cookies['selfpay_session']['httponly'])
        self.assertEqual(self.post('search', {'location': 'Ohio'}, csrf='wrong').status_code, 403)
        self.assertEqual(self.post('search', {'location': 'Ohio', 'scope': 'state'}).status_code, 200)
        self.assertEqual(self.post('preferences', {'location': 'Ohio'}).status_code, 403)
        self.assertEqual(self.post('logout').status_code, 200)
        self.assertEqual(self.client.get('/api/me').status_code, 401)

    def test_registration_login_and_owner_isolation(self):
        self.guest()
        old_token = self.client.cookies['selfpay_session'].value
        credentials = {'name': 'Synthetic Test', 'email': 'synthetic@example.com', 'password': 'Synthetic-only-passphrase!'}
        response = self.post('register', credentials)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertNotIn('token', response.json())
        self.assertIsNone(core.session(old_token, self.db))
        self.assertEqual(self.post('preferences', {'location': 'Ohio'}).status_code, 200)
        saved = self.post('conversations', {'search': {'location': 'Ohio', 'scope': 'state'}, 'messages': []})
        self.assertEqual(saved.status_code, 200, saved.content)
        other = Client()
        other_auth = self.post('register', {**credentials, 'email': 'other@example.com'}, client=other, csrf='').json()
        forbidden = other.delete('/api/conversations/' + saved.json()['id'], HTTP_ORIGIN='http://localhost:5173', HTTP_X_CSRF_TOKEN=other_auth['csrf'])
        self.assertEqual(forbidden.status_code, 404)
        self.assertEqual(self.post('logout').status_code, 200)
        self.csrf = ''
        self.assertEqual(self.post('login', credentials).status_code, 200)
        self.assertEqual(self.client.get('/api/preferences').json()['location'], 'Ohio')
        self.assertEqual(len(self.client.get('/api/conversations').json()['conversations']), 1)

    def test_booking_email_verification_create_and_tracking(self):
        self.guest()
        with core.connect(self.db) as con:
            con.execute("INSERT INTO batches VALUES('api-test-batch','api-test-hash','synthetic',0,'test','{}') ON CONFLICT(id) DO NOTHING")
            con.execute("INSERT INTO settings VALUES('active_batch','api-test-batch') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            center = {'name': 'Synthetic Imaging', 'area': 'Test city', 'email': 'appointments@example.test', 'appointment_email_approved': True}
            con.execute('INSERT INTO entries VALUES(?,?,?,?) ON CONFLICT(batch_id,entry_key) DO UPDATE SET payload=excluded.payload', ('api-test-batch', 'api-test-clinic', 'test', json.dumps(center)))
        with patch('booking_mail.send_patient_code') as sender:
            issued = self.post('booking/email-code', {'email': 'patient@example.test'})
            self.assertEqual(issued.status_code, 200, issued.content)
            code = sender.call_args.args[1]
        verified = self.post('booking/email-verify', {'verification_id': issued.json()['verification_id'], 'code': code})
        self.assertEqual(verified.status_code, 200)
        from datetime import datetime, timedelta
        data = {**verified.json(), 'clinic_id': 'api-test-clinic', 'idempotency_key': 'test-request', 'first_name': 'Synthetic', 'phone': '6145550142', 'service': 'MRI', 'exam': 'Knee', 'contrast': 'without', 'timezone': 'America/New_York', 'consent': True, 'slots': [{'date': (datetime.now() + timedelta(days=3)).date().isoformat(), 'period': 'morning'}]}
        result = self.post('booking/create', data)
        self.assertEqual(result.status_code, 201, result.content)
        self.assertEqual(result.json()['delivery'], 'suppressed')
        self.assertEqual(self.post('booking/create', data).json()['id'], result.json()['id'])
        tracking = self.post('booking/patient', {'id': result.json()['id'], 'token': result.json()['tracking_token']})
        self.assertEqual(tracking.status_code, 200)
        self.assertNotIn('phone', tracking.json())
        self.assertEqual(self.post('booking/preview', {'id': result.json()['id'], 'token': result.json()['tracking_token']}).status_code, 404)

    def test_bad_payload_limits_and_secure_production_cookie(self):
        response = self.client.post('/api/guest', '[]', content_type='application/json', HTTP_ORIGIN='http://localhost:5173')
        self.assertEqual(response.status_code, 400)
        with override_settings(DEBUG=False, SESSION_COOKIE_NAME='__Host-selfpay_session', SECURE_SSL_REDIRECT=False):
            response = self.post('guest')
            cookie = response.cookies['__Host-selfpay_session']
            self.assertTrue(cookie['secure'])
            self.assertTrue(cookie['httponly'])
            self.assertEqual(cookie['path'], '/')
            self.assertFalse(cookie['domain'])

    def test_drf_validation_media_types_and_method_contract(self):
        self.guest()
        response = self.post('search', {'radius': 13})
        self.assertEqual(response.status_code, 400)
        self.assertIn('radius', response.json()['details'])
        malformed = self.client.post('/api/search', '{bad json', content_type='application/json',
                                     HTTP_ORIGIN='http://localhost:5173', HTTP_X_CSRF_TOKEN=self.csrf)
        self.assertEqual(malformed.status_code, 400)
        wrong_media = self.client.post('/api/search', 'location=Ohio', content_type='text/plain',
                                       HTTP_ORIGIN='http://localhost:5173', HTTP_X_CSRF_TOKEN=self.csrf)
        self.assertEqual(wrong_media.status_code, 415)
        oversized = self.client.post('/api/search', json.dumps({'location': 'x' * 100001}),
                                     content_type='application/json', HTTP_ORIGIN='http://localhost:5173',
                                     HTTP_X_CSRF_TOKEN=self.csrf)
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(self.client.get('/api/search').status_code, 405)
        self.assertEqual(self.client.get('/api/not-an-endpoint').status_code, 404)
        self.assertEqual(self.client.get('/api/catalog', HTTP_ACCEPT='text/html').status_code, 406)
        metadata = self.client.options('/api/search', HTTP_ACCEPT='application/json')
        self.assertEqual(metadata.status_code, 200)
        self.assertIn('location', metadata.json()['actions']['POST'])

    def test_drf_throttling_returns_retry_after(self):
        import time
        with patch('api.throttles.time.time', return_value=time.time()), patch('api.throttles.GuestThrottle.limit', 1):
            self.guest()
            response = self.post('guest')
        self.assertEqual(response.status_code, 429)
        self.assertGreater(int(response['Retry-After']), 0)
        self.assertIn('error', response.json())

    def test_booking_capabilities_work_without_a_session_but_reject_forgery(self):
        # A patient capability is independent of a browser login. Invalid
        # capabilities are rejected by the booking service, not session auth.
        anonymous = Client(enforce_csrf_checks=True)
        response = self.post('booking/patient', {'id': 'missing', 'token': 'forged'}, client=anonymous)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.post('booking/options', {'clinic_id': 'missing'}, client=anonymous).status_code, 401)

if __name__ == '__main__':
    unittest.main()
