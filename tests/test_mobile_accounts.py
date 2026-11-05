"""New public auth contract, proof binding and non-destructive legacy setup."""
import json
import unittest
import time
from unittest.mock import patch
import accounts
import booking
import core
import mobile_accounts
from tests import test_api as api_fixtures
from tests.mobile_fixtures import proof


class MobileAccountTests(unittest.TestCase):
    setUp = api_fixtures.APITests.setUp
    tearDown = api_fixtures.APITests.tearDown
    post = api_fixtures.APITests.post

    def credentials(self, phone='+15550123333', username='chosen_username'):
        return {'username':username,'name':'Synthetic Patient','password':'Synthetic-only-passphrase!',
                'zip_code':'45202', **proof(self.db, phone)}

    def test_registration_login_and_profiles_never_require_or_return_patient_email(self):
        data = self.credentials()
        result = self.post('register', data)
        self.assertEqual(result.status_code, 201, result.content)
        member = result.json()['member']
        self.assertEqual(member['username'], 'chosen_username')
        self.assertTrue(member['mobile_verified'])
        self.assertNotIn('email', member)
        self.assertNotIn('email', self.client.get('/api/me').json()['member'])
        self.assertNotIn('email', self.client.get('/api/dashboard').json()['profile'])
        with core.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT email FROM members WHERE id=?',(member['id'],)).fetchone()['email'], 'mobile:'+member['id'])
        self.post('logout'); self.csrf = ''
        login = self.post('login', {'username':'CHOSEN_USERNAME','password':data['password']})
        self.assertEqual(login.status_code, 200, login.content)
        self.assertEqual(login.json()['member']['id'], member['id'])
        self.assertEqual(self.post('login', {'username':'chosen_username','password':'wrong'}).status_code,401)

    @patch('accounts.throttle')
    def test_missing_forged_changed_expired_and_reused_proofs_cannot_create_accounts(self, throttle):
        data = self.credentials()
        for change in ({'verification_token':'forged'}, {'phone':'+15550124444'}, {'challenge':'X'*43}):
            with self.subTest(change=change):
                self.assertEqual(self.post('register',{**data,**change}).status_code,403)
        with core.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM user_accounts').fetchone()['n'],0)
        with patch('booking_phone.time.time', return_value=10**12):
            self.assertEqual(self.post('register',data).status_code,403)
        self.assertEqual(self.post('register',data).status_code,201)
        self.assertEqual(self.post('register',{**data,'username':'another_name'}).status_code,403)

    def test_username_unique_case_insensitive_and_duplicate_phone_rejected(self):
        self.assertEqual(self.post('register', self.credentials()).status_code,201)
        self.assertEqual(self.post('register',self.credentials('+15550124444','CHOSEN_USERNAME')).status_code,409)
        self.assertEqual(self.post('register',self.credentials('+15550123333','another_name')).status_code,409)

    def test_otp_http_provider_failure_and_wrong_code_do_not_verify(self):
        import booking_sms
        with patch('booking_sms.configuration',side_effect=booking_sms.SMSUnavailable('Unavailable')):
            self.assertEqual(self.post('registration/mobile/code',{'phone':'+15550123333'}).status_code,503)
        with patch('booking_sms.configuration',return_value={}),patch('booking_sms.send_code',return_value='VE'+'1'*32):
            issued = self.post('registration/mobile/code',{'phone':'+15550123333'})
        self.assertEqual(issued.status_code,200)
        with patch('booking_sms.check_code',return_value=False):
            self.assertEqual(self.post('registration/mobile/verify',{**issued.json(),'code':'000000'}).status_code,400)
        with core.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM mobile_accounts').fetchone()['n'],0)

    def test_legacy_setup_preserves_member_requests_password_and_revokes_old_session(self):
        legacy = accounts.authenticate(self.db, {'name':'Existing Patient','email':'historical@example.com','phone':'+1 (555) 012-3333',
                                               'password':'Synthetic-only-passphrase!'}, 'register','test')
        ident = legacy['member']['id']
        self.assertTrue(legacy['member']['setup_required'])
        with core.connect(self.db) as db:
            db.execute("INSERT INTO preferences(member_id,tenant,payload) VALUES(?,'selfpay',?)", (ident,json.dumps({'location':'Ohio'})))
        payload = {'clinic_name':'Historical Synthetic Center','clinic_id':'legacy-clinic','location':'Ohio',
                   'first_name':'Existing Patient','phone':'+15550123333','patient_email':'historical@example.com',
                   'price_text':'Confirm price','service':'MRI','exam':'','contrast':'unknown','slots':[],
                   'timezone':'America/New_York','patient_token':'historical-patient-token'}
        with core.connect(self.db) as db:
            db.execute("INSERT INTO booking_requests(id,owner_hash,idempotency_hash,input_hash,payload,patient_hash,clinic_hash,status,delivery,created,link_expires) VALUES(?,?,?,?,?,?,?,'requested','suppressed',?,?)",
                       ('MSP-LEGACY',booking.digest(ident),'legacy-key','legacy-input',booking.pack(payload),'legacy-patient','legacy-center',time.time(),time.time()+3600))
        bad = self.credentials()
        bad['password'] = 'Wrong-password-123!'
        self.assertEqual(self.post('account/setup',bad).status_code,401)
        # The same legitimate OTP proof is retained after a wrong password, not consumed.
        data = {**bad,'password':'Synthetic-only-passphrase!'}
        result = self.post('account/setup',data)
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['member']['id'],ident)
        self.assertFalse(result.json()['member']['setup_required'])
        self.assertIsNone(core.session(legacy['token'],self.db))
        self.assertEqual(self.client.get('/api/preferences').json()['location'],'Ohio')
        dashboard = self.client.get('/api/dashboard')
        self.assertEqual(dashboard.status_code,200,dashboard.content)
        self.assertEqual(dashboard.json()['total'],1)
        self.assertEqual(dashboard.json()['requests'][0]['id'],'MSP-LEGACY')
        self.assertNotIn('historical@example.com',str(dashboard.json()))
        with core.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT email FROM user_accounts WHERE member_id=?',(ident,)).fetchone()['email'],'mobile:'+ident)
        # Setup preserves the member ID that owns all existing booking hashes.
        self.assertEqual(booking.digest(ident),booking.digest(result.json()['member']['id']))

    def test_unconverted_account_cannot_book_with_an_old_email_proof(self):
        data = accounts.authenticate(self.db,{'name':'Existing','email':'existing@example.com','password':'Synthetic-only-passphrase!'},'register','test')
        with core.connect(self.db) as db:
            with self.assertRaises(booking.BookingError):
                mobile_accounts.booking_phone_for(db,data['member']['id'])
