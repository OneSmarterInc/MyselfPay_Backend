"""Direct request verification: synthetic US phones and mocked Twilio only."""
from datetime import datetime, timedelta
import json
import os
import unittest
from unittest.mock import patch
import core
import booking
from tests import test_api as fixtures


class USRequestTests(unittest.TestCase):
    setUp = fixtures.APITests.setUp
    tearDown = fixtures.APITests.tearDown
    post = fixtures.APITests.post

    def issue(self, phone='(614) 555-0142'):
        with patch('booking_sms.configuration', return_value={}), patch('booking_sms.send_code', return_value='VE'+'1'*32) as send:
            response = self.post('auth/mobile/code', {'intent':'request', 'phone':phone})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(send.call_args.args[0], '+16145550142')
        return {'intent':'request', 'phone':phone, **response.json(), 'code':'123456'}

    def finish(self, data, approved=True):
        with patch('booking_sms.check_code', return_value=approved):
            return self.post('auth/mobile/verify', data)

    def test_new_and_existing_phone_continue_without_signup_or_name(self):
        result = self.finish(self.issue())
        self.assertEqual(result.status_code, 200, result.content)
        ident = result.json()['member']['id']
        self.assertTrue(result.json()['member']['booking_eligible'])
        self.assertEqual(result.json()['member']['username'], '+16145550142')
        self.assertNotIn('email', result.json()['member'])
        self.post('logout'); self.csrf=''
        again = self.finish(self.issue('+1 614 555 0142'))
        self.assertEqual(again.json()['member']['id'], ident)
        with core.connect(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM members').fetchone()['n'], 1)

    def test_international_country_codes_can_verify_and_request(self):
        import mobile_accounts
        for phone in ('+14165550123', '+919876543210', '+447400123456'):
            with self.subTest(phone=phone):
                with patch('booking_sms.configuration', return_value={}), patch('booking_sms.send_code', return_value='VE'+'1'*32) as send:
                    response = self.post('auth/mobile/code', {'intent':'request','phone':phone})
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(send.call_args.args[0], phone)
                result = self.finish({'intent':'request','phone':phone,**response.json(),'code':'123456'})
                self.assertEqual(result.status_code, 200, result.content)
                self.assertTrue(result.json()['member']['booking_eligible'])
                with core.connect(self.db) as db:
                    self.assertEqual(mobile_accounts.booking_phone_for(db,result.json()['member']['id']),phone)
                self.post('logout'); self.csrf=''

    def test_invalid_and_changed_phone_rejected(self):
        with patch('booking_sms.send_code') as send:
            for phone in ('+15550123456', 'hello', '+16145550142x1'):
                response = self.post('auth/mobile/code', {'intent':'request', 'phone':phone})
                self.assertEqual(response.status_code, 400, response.content)
            send.assert_not_called()
        data = self.issue()
        self.assertEqual(self.finish(data, False).status_code, 400)
        self.assertEqual(self.finish({**data,'phone':'+16145550143'}).status_code, 403)
        self.assertEqual(self.finish(self.issue()).status_code, 200)

    def test_request_can_be_created_without_patient_name_email_or_password(self):
        self.finish(self.issue())
        with core.connect(self.db) as db:
            db.execute("INSERT INTO batches VALUES('us-test','us-hash','synthetic',0,'published','{}')")
            db.execute("INSERT INTO settings(key,value) VALUES('active_batch','us-test') ON CONFLICT(key) DO UPDATE SET value=excluded.value")
            db.execute('INSERT INTO entries VALUES(?,?,?,?)', ('us-test','clinic','hash',json.dumps({'name':'Synthetic center'})))
        data = {'clinic_id':'clinic', 'idempotency_key':'synthetic-us-request', 'service':'Please contact me',
                'consent':True, 'slots':[{'date':(datetime.now()+timedelta(days=2)).date().isoformat(),'period':'morning'}]}
        with patch.dict(os.environ, {'BOOKING_EMAIL_ENABLED':'false','SECURE_MAIL_ENABLED':'false'}):
            response = self.post('booking/create', data)
        self.assertEqual(response.status_code, 201, response.content)
        with core.connect(self.db) as db:
            payload = booking.unpack(booking.get_row(db,response.json()['id']))
            self.assertEqual(payload['phone'], '+16145550142')
            self.assertEqual(payload['first_name'], '')

    def test_cross_purpose_replay_and_disabled_accounts_cannot_request(self):
        data = self.issue('+16145550142')
        self.assertEqual(self.finish({**data,'intent':'login'}).status_code, 410)
        data = self.issue()
        result = self.finish(data)
        self.assertEqual(self.finish(data).status_code, 410)
        with core.connect(self.db) as db:
            db.execute('INSERT INTO settings VALUES(?,?)', ('account-disabled:'+result.json()['member']['id'],'1'))
        with patch('booking_sms.send_code') as send:
            self.assertEqual(self.post('auth/mobile/code',{'intent':'request','phone':'+16145550142'}).status_code, 403)
            send.assert_not_called()
