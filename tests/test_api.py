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
            con.executescript((Path(core.__file__).parent / 'ops/booking-outbox.sql').read_text())
            con.executescript((Path(core.__file__).parent / 'ops/booking-phone.sql').read_text())
            con.executescript((Path(core.__file__).parent / 'ops/booking-verified-email.sql').read_text())
            con.executescript((Path(core.__file__).parent / 'ops/center-portal.sql').read_text())
            # CI database is disposable and must never point at production.
            for table in ['booking_verified_emails', 'booking_email_checks', 'booking_mail_limits', 'booking_requests', 'corrections', 'preferences', 'conversations', 'guest_usage', 'sessions', 'user_accounts', 'members', 'login_limits']:
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

    def test_center_cookie_login_csrf_and_logout(self):
        with core.connect(self.db) as db:
            db.execute("INSERT INTO batches VALUES('center-batch','center-hash','test',0,'published','{}') ON CONFLICT(id) DO NOTHING")
            db.execute("INSERT INTO settings(key,value) VALUES('active_batch','center-batch') ON CONFLICT(key) DO UPDATE SET value='center-batch'")
            db.execute('INSERT INTO entries VALUES(?,?,?,?) ON CONFLICT(batch_id,entry_key) DO UPDATE SET payload=excluded.payload',
                       ('center-batch','synthetic-center','center-hash',json.dumps({'name':'Synthetic Center','email':'center@example.test'})))
        self.assertEqual(self.client.get('/api/center/dashboard').status_code, 401)
        self.assertEqual(self.post('center/code',{'email':'center@example.test'},origin='https://evil.example').status_code,403)
        with patch('booking_mail.send_message') as send:
            issued=self.post('center/code',{'email':'center@example.test'})
        self.assertEqual(issued.status_code,200,issued.content)
        code=send.call_args.args[0].get_content().split('code is: ')[1].split()[0]
        verified=self.post('center/verify',{'verification_id':issued.json()['verification_id'],'code':code})
        self.assertEqual(verified.status_code,200,verified.content)
        self.assertTrue(self.client.cookies['selfpay_center']['httponly'])
        dashboard=self.client.get('/api/center/dashboard')
        self.assertEqual(dashboard.status_code,200,dashboard.content)
        self.assertEqual(dashboard.json()['centers'][0]['name'],'Synthetic Center')
        self.assertNotIn('csrf',dashboard.json())
        self.assertEqual(self.post('center/logout').status_code,403)
        logout=self.client.post('/api/center/logout','{}',content_type='application/json',HTTP_ORIGIN='http://localhost:5173',HTTP_X_CENTER_CSRF=dashboard.json()['center_csrf'])
        self.assertEqual(logout.status_code,200,logout.content)
        self.assertEqual(self.client.get('/api/center/dashboard').status_code,401)

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
        registered = self.post('register', {'name': 'Synthetic Booker', 'email': 'booker@example.com', 'password': 'Synthetic-only-passphrase!'})
        self.assertEqual(registered.status_code, 201, registered.content)
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
        with patch('booking_sms.configuration',return_value={}),patch('booking_sms.send_code',return_value='VE'+'1'*32):
            phone=self.post('booking/phone-code',{'phone':'+16145550142'})
        self.assertEqual(phone.status_code,200,phone.content)
        with patch('booking_sms.check_code',return_value=True):
            checked=self.post('booking/phone-verify',{'verification_id':phone.json()['verification_id'],'code':'123456'})
        self.assertEqual(checked.status_code,200,checked.content)
        data.update(phone='+16145550142',phone_verification_id=checked.json()['verification_id'],phone_verification_token=checked.json()['verification_token'])
        result = self.post('booking/create', data)
        self.assertEqual(result.status_code, 201, result.content)
        self.assertEqual(result.json()['delivery'], 'suppressed')
        self.assertEqual(self.post('booking/create', data).json()['id'], result.json()['id'])
        tracking = self.post('booking/patient', {'id': result.json()['id'], 'token': result.json()['tracking_token']})
        self.assertEqual(tracking.status_code, 200)
        self.assertNotIn('phone', tracking.json())
        self.assertEqual(self.post('booking/preview', {'id': result.json()['id'], 'token': result.json()['tracking_token']}).status_code, 404)
        # Migration recovers verification from an earlier successfully consumed challenge.
        with core.connect(self.db) as con:
            con.execute('DELETE FROM booking_verified_emails')
        from importlib import import_module
        import_module('api.migrations.0007_saved_email_verification').create_schema(None, None)
        # The verified address is retained across logout/login and no proof or SMS is needed.
        self.assertEqual(self.post('logout').status_code, 200)
        self.csrf = ''
        self.assertEqual(self.post('login', {'email':'booker@example.com','password':'Synthetic-only-passphrase!'}).status_code, 200)
        saved = self.post('booking/options', {'clinic_id':'api-test-clinic'})
        self.assertEqual(saved.json()['verified_email'], 'patient@example.test')
        repeat = {k:v for k,v in data.items() if 'verification_' not in k}
        repeat.update(email='patient@example.test', phone='', idempotency_key='saved-email-repeat')
        repeat.pop('timezone')
        self.assertEqual(self.post('booking/create', repeat).status_code, 201)
        dashboard=self.client.get('/api/dashboard').json()
        self.assertEqual(dashboard['profile']['email'],'booker@example.com')
        self.assertEqual(dashboard['total'],2)
        self.assertEqual(dashboard['stats']['requested'],2)
        self.assertEqual(self.client.get('/api/dashboard?filter=open').json()['total'],2)
        other=Client(enforce_csrf_checks=True)
        self.assertEqual(other.get('/api/dashboard').status_code,401)
        self.post('register',{'name':'Other Dashboard User','email':'dashboard-other@example.com','password':'Synthetic-only-passphrase!'},client=other,csrf='')
        private=other.get('/api/dashboard').json()
        self.assertEqual(private['total'],0)
        self.assertNotIn('Synthetic Imaging',str(private))

        repeat.update(email='unverified@example.test', idempotency_key='forged-email')
        self.assertEqual(self.post('booking/create', repeat).status_code, 403)
        # Email-link approval cannot be performed before center OTP verification.
        request_id=result.json()['id']
        with booking.connection(self.db) as con:
            clinic_token=booking.unpack(booking.get_row(con,request_id))['clinic_token']
        self.assertEqual(self.post('booking/status',{'id':request_id,'session':'unverified','status':'scheduled'}).status_code,404)
        with patch('booking_delivery.mode',return_value='enabled'), patch('booking_mail.send_clinic_code') as center_sender:
            issued=self.post('booking/code',{'id':request_id,'token':clinic_token})
            self.assertEqual(issued.status_code,200,issued.content)
            self.assertEqual(center_sender.call_args.args[0],'appointments@example.test')
            center_code=center_sender.call_args.args[2]
        checked=self.post('booking/verify',{'id':request_id,'token':clinic_token,'code':center_code})
        self.assertEqual(checked.status_code,200)
        approved=self.post('booking/status',{'id':request_id,'session':checked.json()['session'],'status':'scheduled'})
        self.assertEqual(approved.status_code,200,approved.content)
        dashboard=self.client.get('/api/dashboard?filter=approved').json()
        self.assertEqual(dashboard['total'],1)
        self.assertEqual(dashboard['stats']['scheduled'],1)
        self.assertEqual(dashboard['requests'][0]['status'],'scheduled')
        self.assertEqual(dashboard['requests'][0]['contact_email'],'patient@example.test')
        self.assertIsNotNone(dashboard['requests'][0]['approved_at'])



    def test_bad_payload_limits_and_secure_production_cookie(self):
        response = self.client.post('/api/guest', '[]', content_type='application/json', HTTP_ORIGIN='http://localhost:5173')
        self.assertEqual(response.status_code, 400)
        with override_settings(DEBUG=False, SELFPAY_SESSION_COOKIE_NAME='__Host-selfpay_session', SECURE_SSL_REDIRECT=False):
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

    def test_guest_cannot_start_booking_or_send_verification_email(self):
        self.guest()
        with patch('booking_mail.send_patient_code') as sender, patch('booking.create') as create:
            for action in ('options', 'email-code', 'email-verify', 'phone-code', 'phone-verify', 'create'):
                response = self.post('booking/' + action)
                self.assertEqual(response.status_code, 403, response.content)
                self.assertIn('user account', response.json()['error'])
            sender.assert_not_called()
            create.assert_not_called()

    def test_national_includes_directory_centers_from_every_state_without_exam(self):
        self.guest()
        with core.connect(self.db) as con:
            con.execute("INSERT INTO batches VALUES('national-test','national-hash','synthetic',0,'test','{}')")
            con.execute("INSERT INTO settings VALUES('active_batch','national-test') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            for state in ('OH', 'CA', 'NY'):
                entry = {'name': 'Synthetic ' + state, 'states': state, 'area': state, 'mri': 'Available; confirm exam', 'ct': 'Available; confirm exam'}
                con.execute('INSERT INTO entries VALUES(?,?,?,?)', ('national-test', state, state, json.dumps(entry)))
        result = self.post('search', {'location': 'National'}).json()
        self.assertEqual({entry['states'] for entry in result['nearby_unpriced']}, {'OH', 'CA', 'NY'})
        self.assertEqual(result['results_heading'], 'Imaging centers across all states')
        priced = self.post('search', {'location': 'National', 'type': 'fixed'}).json()
        self.assertEqual(priced['nearby_unpriced'], [])

    def test_only_location_is_required_for_search(self):
        self.guest()
        for data in ({}, {'location':''}):
            response = self.post('search', data)
            self.assertEqual(response.status_code, 400)
            self.assertIn('location', response.json()['details'])
        for data in ({'location':'Ohio'}, {'location':'National', 'mod':'MRI'}, {'location':'Ohio', 'contrast':'without'}, {'location':'Ohio', 'body':'Knee'}, {'location':'Ohio', 'budget':500}):
            response = self.post('search', data)
            self.assertEqual(response.status_code, 200, response.content)

    def test_predefined_chat_questions_only_and_guest_quota(self):
        from chat_questions import QUESTIONS
        self.guest()
        rejected = self.post('agent', {'message':'What scan should I get?'})
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(self.client.get('/api/guest-usage').json()['usage'], {})
        conversation = None
        for question, preset in QUESTIONS.items():
            response = self.post('agent', {'message':question, 'search':{'location':'National','mod':'CT','budget':1}, 'conversation_id':conversation})
            self.assertEqual(response.status_code, 200, response.content)
            data = response.json()
            conversation = data['conversation_id']
            self.assertEqual(data['agent_mode'], 'predefined-questions')
            self.assertEqual(data['predefined_question'], question)
            self.assertEqual(data['location_suggestions'], [])
            self.assertEqual(data['search']['mod'], 'MRI')
            self.assertEqual(data['search']['budget'], preset['search']['budget'])
            self.assertEqual(data['search']['location'], preset['search']['location'])
            self.assertIn('No matching listings', data['reply'])
        question = next(iter(QUESTIONS))
        for _ in range(2):
            self.assertEqual(self.post('agent', {'message':question, 'conversation_id':conversation}).status_code, 200)
        self.assertEqual(self.post('agent', {'message':question, 'conversation_id':conversation}).status_code, 429)

    def test_registered_chat_also_rejects_free_text(self):
        self.assertEqual(self.post('register', {'name':'Synthetic Chat User','email':'chat@example.com','password':'Synthetic-only-passphrase!'}).status_code, 201)
        self.assertEqual(self.post('agent', {'message':'Hello arbitrary question'}).status_code, 400)
        response = self.post('agent', {'message':'  KNEE MRI NEAR DAYTON  '})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['predefined_question'], 'Knee MRI near Dayton')

    def test_budget_is_optional_and_accepts_only_up_to_six_digits(self):
        self.guest()
        for value in ('',0,1,999999,'999999'):
            response = self.post('search',{'location':'Ohio','budget':value})
            self.assertEqual(response.status_code,200,response.content)
        for value in (1000000,'1234567','1e3',12.5,'12.50','letters',-1):
            response = self.post('search',{'location':'Ohio','budget':value})
            self.assertEqual(response.status_code,400,response.content)
            self.assertIn('budget',response.json()['details'])

    def test_admin_suspension_blocks_existing_sessions_and_new_login(self):
        credentials={'name':'Suspend Test','email':'suspend@example.com','password':'Synthetic-only-passphrase!'}
        member=self.post('register',credentials).json()['member']
        self.assertEqual(self.client.get('/api/me').status_code,200)
        with core.connect(self.db) as con:
            con.execute("INSERT INTO settings(key,value) VALUES(?,'1')",('account-disabled:'+member['id'],))
        self.assertEqual(self.client.get('/api/me').status_code,401)
        self.assertEqual(self.post('login',credentials).status_code,401)
        with core.connect(self.db) as con:
            con.execute("UPDATE settings SET value='0' WHERE key=?",('account-disabled:'+member['id'],))
        self.assertEqual(self.post('login',credentials).status_code,200)

    def test_patient_otp_requests_are_not_rate_limited_by_the_application(self):
        self.post('register',{'name':'OTP Test','email':'otp@example.com','password':'Synthetic-only-passphrase!'})
        with patch('booking_mail.send_patient_code') as send:
            for _ in range(8):
                response=self.post('booking/email-code',{'email':'patient@example.test'})
                self.assertEqual(response.status_code,200,response.content)
            self.assertEqual(send.call_count,8)
        with patch('booking_sms.configuration',return_value={}),patch('booking_sms.send_code',return_value='VE'+'1'*32) as send:
            for _ in range(8):
                response=self.post('booking/phone-code',{'phone':'+15550102222'})
                self.assertEqual(response.status_code,200,response.content)
            self.assertEqual(send.call_count,8)

if __name__ == '__main__':
    unittest.main()
