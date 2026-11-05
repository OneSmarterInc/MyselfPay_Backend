import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import accounts
import booking
import booking_sms
import core
import mobile_accounts
import password_reset
from tests import test_api as fixtures
from tests.mobile_fixtures import proof


class PasswordResetTests(unittest.TestCase):
    tearDown = fixtures.APITests.tearDown
    post = fixtures.APITests.post

    def setUp(self):
        fixtures.APITests.setUp(self)
        password_reset.initialize(self.db)
        self.phone = '+15550123456'
        self.old = 'Synthetic-old-password!'
        self.new = 'Synthetic-new-password!'
        data = {'username':'reset_user','name':'Synthetic Reset','password':self.old,'zip_code':'45202',**proof(self.db,self.phone)}
        response = self.post('register',data)
        self.assertEqual(response.status_code,201,response.content)
        self.member = response.json()['member']['id']
        self.token = self.client.cookies['selfpay_session'].value

    def issue(self):
        with patch('booking_sms.configuration',return_value={}),patch('booking_sms.send_code',return_value='VE'+'1'*32):
            response = self.post('password/code',{'username':'RESET_USER','phone':self.phone})
        self.assertEqual(response.status_code,200,response.content)
        return response.json()

    def verified(self):
        issued = self.issue()
        with patch('booking_sms.check_code',return_value=True):
            response = self.post('password/verify',{**issued,'code':'123456'})
        self.assertEqual(response.status_code,200,response.content)
        return {**issued,**response.json(),'password':self.new,'confirm_password':self.new}

    def test_reset_preserves_account_revokes_sessions_and_changes_login(self):
        second = mobile_accounts.authenticate(self.db,{'username':'reset_user','password':self.old},'login','other-client')
        data = self.verified()
        response = self.post('password/reset',data)
        self.assertEqual(response.status_code,200,response.content)
        self.assertNotIn('token',response.json())
        self.assertIsNone(core.session(self.token,self.db))
        self.assertIsNone(core.session(second['token'],self.db))
        self.assertEqual(self.client.get('/api/me').status_code,401)
        self.assertEqual(self.post('login',{'username':'reset_user','password':self.old}).status_code,401)
        fresh = self.post('login',{'username':'reset_user','password':self.new})
        self.assertEqual(fresh.status_code,200,fresh.content)
        self.assertEqual(fresh.json()['member']['id'],self.member)
        self.assertEqual(fresh.json()['member']['zip_code'],'45202')
        self.assertEqual(self.post('password/reset',data).status_code,410)

    def test_unknown_or_mismatched_details_do_not_send_or_disclose_account(self):
        expected_fields=list(self.issue())
        with patch('booking_sms.send_code') as send:
            for data in ({'username':'unknown_user','phone':self.phone},{'username':'reset_user','phone':'+15550129999'}):
                response=self.post('password/code',data)
                self.assertEqual(response.status_code,200,response.content)
                self.assertEqual(response.json()['message'],password_reset.MESSAGE)
                self.assertEqual(list(response.json()),expected_fields)
            send.assert_not_called()
        self.assertEqual(self.post('password/verify',{**response.json(),'code':'123456'}).status_code,410)

    def test_forged_replayed_registration_and_mixed_challenge_proofs_are_rejected(self):
        data=self.verified()
        self.assertEqual(self.post('password/reset',{**data,'verification_token':'forged'}).status_code,403)
        self.assertEqual(self.post('password/reset',{**data,'challenge':'X'*43}).status_code,410)
        registration = proof(self.db,self.phone)
        self.assertEqual(self.post('password/reset',{**data,'verification_id':registration['verification_id'],'verification_token':registration['verification_token']}).status_code,410)
        # A registration proof cannot authorize resetting a password.
        with booking.connection(self.db,write=True) as db:
            db.execute('UPDATE password_reset_requests SET verification_id=? WHERE challenge_hash=?',(registration['verification_id'],booking.digest(data['challenge'])))
        self.assertEqual(self.post('password/reset',{**data,'verification_id':registration['verification_id'],'verification_token':registration['verification_token']}).status_code,403)

    def test_wrong_expired_otp_and_password_validation_never_change_password(self):
        issued=self.issue()
        with patch('booking_sms.check_code',return_value=False):
            self.assertEqual(self.post('password/verify',{**issued,'code':'000000'}).status_code,400)
        with patch('password_reset.time.time',return_value=time.time()+1000):
            self.assertEqual(self.post('password/verify',{**issued,'code':'123456'}).status_code,410)
        data=self.verified()
        self.assertEqual(self.post('password/reset',{**data,'password':'short','confirm_password':'short'}).status_code,400)
        self.assertEqual(self.post('password/reset',{**data,'confirm_password':'Different-password!'}).status_code,400)
        self.assertEqual(self.post('login',{'username':'reset_user','password':self.old}).status_code,200)

    def test_password_changes_disable_account_or_mobile_changes_invalidate_intent(self):
        issued=self.issue()
        with core.connect(self.db) as db:
            db.execute('UPDATE members SET salt=? WHERE id=?',('ab'*16,self.member))
        with patch('booking_sms.check_code') as send:
            self.assertEqual(self.post('password/verify',{**issued,'code':'123456'}).status_code,410)
            send.assert_not_called()

    def test_one_reset_invalidates_other_pending_resets_and_disabled_accounts_stay_disabled(self):
        first=self.verified(); second=self.verified()
        self.assertEqual(self.post('password/reset',first).status_code,200)
        self.assertEqual(self.post('password/reset',second).status_code,410)
        with core.connect(self.db) as db:
            db.execute("INSERT INTO settings VALUES(?,'1')",('account-disabled:'+self.member,))
        with patch('booking_sms.send_code') as send:
            self.assertEqual(self.post('password/code',{'username':'reset_user','phone':self.phone}).status_code,200)
            send.assert_not_called()

    def test_concurrent_resets_have_one_winner(self):
        first=self.verified(); second=self.verified()
        def apply(data):
            try:
                password_reset.save(self.db,data)
                return 200
            except accounts.AccountError as exc:
                return exc.status
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(apply,[first,second]))
        self.assertEqual(sorted(results),[200,410])

    def test_a_concurrent_old_password_login_cannot_survive_reset(self):
        data=self.verified()
        def login():
            try:
                return mobile_accounts.authenticate(self.db,{'username':'reset_user','password':self.old},'login','concurrent')
            except accounts.AccountError:
                return None
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempt=pool.submit(login)
            reset=pool.submit(password_reset.save,self.db,data)
            result=attempt.result(); self.assertTrue(reset.result()['ok'])
        if result:
            self.assertIsNone(core.session(result['token'],self.db))

    def test_provider_failure_cannot_reset_password(self):
        with patch('booking_sms.configuration',side_effect=booking_sms.SMSUnavailable('Unavailable')):
            response=self.post('password/code',{'username':'reset_user','phone':self.phone})
        self.assertEqual(response.status_code,503)
        self.assertEqual(self.post('login',{'username':'reset_user','password':self.old}).status_code,200)
