"""Real HTTP OTP-only registration/login; SMS is always simulated."""
import unittest
from unittest.mock import patch
import accounts
import booking
import core
from tests import test_api as fixtures


class PhoneOnlyTests(unittest.TestCase):
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown
    post = fixtures.APITests.post

    def issue(self, intent='register', phone='+15550128888', name='Jamie'):
        data={'intent':intent,'phone':phone,'name':name}
        with patch('booking_sms.configuration',return_value={}), patch('booking_sms.send_code',return_value='VE'+'1'*32):
            response=self.post('auth/mobile/code',data)
        self.assertEqual(response.status_code,200,response.content)
        return {**data,**response.json(),'code':'123456'}

    def finish(self,data,approved=True):
        with patch('booking_sms.check_code',return_value=approved):
            return self.post('auth/mobile/verify',data)

    def test_register_then_login_without_username_password_email_or_zip(self):
        data=self.issue()
        response=self.finish(data)
        self.assertEqual(response.status_code,201,response.content)
        member=response.json()['member'];ident=member['id']
        self.assertEqual(member['name'],'Jamie')
        self.assertEqual(member['phone'],data['phone'])
        self.assertTrue(member['mobile_verified'])
        self.assertNotIn('email',member)
        self.assertEqual(member['username'],data['phone'])
        with core.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM members').fetchone()['n'],1)
            self.assertTrue(db.execute('SELECT password_hash FROM members').fetchone()['password_hash'].startswith('otp-only$'))
        self.post('logout');self.csrf=''
        logged=self.finish(self.issue('login'))
        self.assertEqual(logged.status_code,200,logged.content)
        self.assertEqual(logged.json()['member']['id'],ident)
        self.assertEqual(self.client.get('/api/me').status_code,200)

    def test_wrong_expired_reused_changed_phone_and_cross_purpose_rejected(self):
        data=self.issue()
        self.assertEqual(self.finish(data,False).status_code,400)
        self.assertEqual(self.finish({**data,'phone':'+15550129999'}).status_code,403)
        # The failed changed-phone finish must not create or log into an account.
        with core.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM members').fetchone()['n'],0)
        data=self.issue()
        self.assertEqual(self.finish({**data,'intent':'login'}).status_code,410)
        self.assertEqual(self.finish(data).status_code,201)
        self.assertEqual(self.finish(data).status_code,410)
        self.post('logout');self.csrf=''
        expired=self.issue('login')
        with core.connect(self.db) as db:
            db.execute('UPDATE booking_phone_checks SET expires=0 WHERE id=?',(expired['verification_id'],))
        self.assertEqual(self.finish(expired).status_code,410)

    def test_duplicate_unknown_disabled_and_untrusted_origin_do_not_send(self):
        self.finish(self.issue())
        with patch('booking_sms.send_code') as send:
            self.assertEqual(self.post('auth/mobile/code',{'intent':'register','name':'Other','phone':'+15550128888'}).status_code,409)
            self.assertEqual(self.post('auth/mobile/code',{'intent':'login','phone':'+15550127777'}).status_code,404)
            self.assertEqual(self.post('auth/mobile/code',{'intent':'login','phone':'+15550128888'},origin='https://evil.example').status_code,403)
            with core.connect(self.db) as db:
                ident=db.execute('SELECT member_id FROM mobile_accounts').fetchone()['member_id']
                db.execute('INSERT INTO settings VALUES(?,?)',('account-disabled:'+ident,'1'))
            self.assertEqual(self.post('auth/mobile/code',{'intent':'login','phone':'+15550128888'}).status_code,403)
            send.assert_not_called()

    def test_legacy_saved_phone_login_preserves_member_and_history_owner(self):
        old=accounts.authenticate(self.db,{'email':'old@example.com','name':'Existing','password':'Synthetic-only-passphrase!','phone':'+15550126666'},'register','test')
        result=self.finish(self.issue('login','+15550126666'))
        self.assertEqual(result.status_code,200,result.content)
        self.assertEqual(result.json()['member']['id'],old['member']['id'])
        self.assertFalse(result.json()['member']['setup_required'])

    def test_sms_unavailable_does_not_create_account(self):
        import booking_sms
        with patch('booking_sms.configuration',side_effect=booking_sms.SMSUnavailable('Unavailable')):
            result=self.post('auth/mobile/code',{'intent':'register','name':'Jamie','phone':'+15550128888'})
        self.assertEqual(result.status_code,503)
        with core.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM members').fetchone()['n'],0)
