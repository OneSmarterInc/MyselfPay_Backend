import time
import unittest
from unittest.mock import patch
import booking
import booking_phone
import booking_sms
import test_booking


class MobileOTPTests(unittest.TestCase):
    def setUp(self):
        self.fixture=test_booking.BookingTests();self.fixture.setUp()
        self.path=self.fixture.db
        self.owner='mobile-test-owner'
        self.phone='+15550129999'

    def tearDown(self): self.fixture.tearDown()

    def request(self):
        with patch('booking_sms.configuration',return_value={}),patch('booking_sms.send_code',return_value='VE'+'2'*32):
            return booking_phone.request_code(self.path,self.owner,self.phone)

    def test_required_phone_owner_and_contact_binding(self):
        for changes in ({'phone_verification_token':None},{'phone':'+15550128888'}):
            with self.assertRaises(booking.BookingError): self.fixture.create({**self.fixture.data,**changes})
        issued=self.request()
        with patch('booking_sms.check_code',return_value=True) as check:
            with self.assertRaises(booking.BookingError): booking_phone.verify_code(self.path,'another-owner',issued['verification_id'],'123456')
            check.assert_not_called()
            proof=booking_phone.verify_code(self.path,self.owner,issued['verification_id'],'123456')
        with booking.connection(self.path) as db:
            normalized=booking_phone.require_verified(db,booking.digest(self.owner),issued['verification_id'],proof['verification_token'],'+1 (555) 012-9999')
            self.assertEqual(normalized,self.phone)
            row=db.execute('SELECT phone_encrypted,proof_hash FROM booking_phone_checks WHERE id=?',(issued['verification_id'],)).fetchone()
            self.assertNotIn(self.phone,row['phone_encrypted'])
            self.assertNotEqual(row['proof_hash'],proof['verification_token'])
        with self.assertRaises(booking.BookingError): booking_phone.verify_code(self.path,self.owner,issued['verification_id'],'123456')

    def test_email_only_booking_without_mobile_proof(self):
        data={k:v for k,v in self.fixture.data.items() if not k.startswith('phone_verification_')}
        data.pop('phone')
        with patch('booking_phone.require_verified') as mobile_check:
            result=self.fixture.create(data)
            self.assertTrue(result['id'])
            mobile_check.assert_not_called()

    def test_resends_and_checks_have_no_application_limits(self):
        with booking.connection(self.path,write=True) as db:
            db.execute('INSERT INTO booking_sms_limits VALUES(?,?,999)',('owner:'+booking.digest(self.owner),int(time.time()//3600)))
        for _ in range(8):issued=self.request()
        with patch('booking_sms.check_code',return_value=False) as check:
            for _ in range(8):
                with self.assertRaises(booking.BookingError) as error:booking_phone.verify_code(self.path,self.owner,issued['verification_id'],'000000')
                self.assertEqual(error.exception.status,400)
            self.assertEqual(check.call_count,8)
        with patch('booking_sms.check_code',return_value=True):
            self.assertIn('verification_token',booking_phone.verify_code(self.path,self.owner,issued['verification_id'],'123456'))

    def test_missing_configuration_and_provider_failure_fail_closed(self):
        with patch('booking_sms.configuration',side_effect=booking_sms.SMSUnavailable('Not configured')):
            with self.assertRaises(booking.BookingError) as error:booking_phone.request_code(self.path,self.owner,self.phone)
            self.assertEqual(error.exception.status,503)
        with patch('booking_sms.configuration',return_value={}),patch('booking_sms.send_code',side_effect=booking_sms.SMSUnavailable('Unavailable')):
            with self.assertRaises(booking.BookingError):booking_phone.request_code(self.path,self.owner,self.phone)
        with booking.connection(self.path) as db:
            row=db.execute('SELECT state FROM booking_phone_checks WHERE owner_hash=?',(booking.digest(self.owner),)).fetchone()
            self.assertEqual(row['state'],'failed')

    def test_expiration_and_single_use_booking(self):
        issued=self.request()
        with patch('booking_phone.time.time',return_value=time.time()+601):
            with self.assertRaises(booking.BookingError):booking_phone.verify_code(self.path,self.owner,issued['verification_id'],'123456')
        first=self.fixture.create()
        self.assertEqual(self.fixture.create()['id'],first['id'])
        data={**self.fixture.data,**self.fixture.verify_email('guest-owner','other@example.test'),'idempotency_key':'new-booking'}
        with self.assertRaises(booking.BookingError):self.fixture.create(data)

    def test_transport_uses_verify_service_and_does_not_return_secret(self):
        values={'api_key_sid':'SK'+'1'*32,'api_key_secret':'synthetic-secret','verify_service_sid':'VA'+'2'*32}
        with patch('booking_sms.configuration',return_value=values),patch('booking_sms.build_opener') as opener:
            response=opener.return_value.open.return_value.__enter__.return_value
            response.read.return_value=b'{"sid":"VE11111111111111111111111111111111","status":"pending"}'
            sid=booking_sms.send_code(self.phone)
            self.assertTrue(sid.startswith('VE'))
            request=opener.return_value.open.call_args.args[0]
            self.assertTrue(request.full_url.startswith('https://verify.twilio.com/v2/Services/VA'))
            self.assertIn(b'Channel=sms',request.data)
            self.assertEqual(opener.return_value.open.call_args.kwargs['timeout'],12)
            self.assertNotIn('synthetic-secret',request.full_url)
            response.read.return_value=b'{"status":"approved"}'
            self.assertTrue(booking_sms.check_code(sid,'123456'))
            self.assertIn(b'VerificationSid=',opener.return_value.open.call_args.args[0].data)

    def test_provider_error_logs_only_safe_status_and_numeric_code(self):
        from urllib.error import HTTPError
        from io import BytesIO, StringIO
        values={'api_key_sid':'SK'+'1'*32,'api_key_secret':'synthetic-secret','verify_service_sid':'VA'+'2'*32}
        provider_error=HTTPError('https://verify.twilio.com',401,'Unauthorized',{},BytesIO(b'{"code":20003,"message":"synthetic-secret +15550129999"}'))
        with patch('booking_sms.configuration',return_value=values),patch('booking_sms.build_opener') as opener,self.assertLogs('booking_sms',level='WARNING') as logs:
            opener.return_value.open.side_effect=provider_error
            with self.assertRaises(booking_sms.SMSUnavailable) as exc:booking_sms.send_code(self.phone)
        self.assertEqual(exc.exception.provider_code,20003)
        self.assertEqual(exc.exception.http_status,401)
        self.assertIn('TwilioCode=20003',logs.output[0])
        self.assertNotIn('synthetic-secret',' '.join(logs.output))
        self.assertNotIn(self.phone,' '.join(logs.output))
        from api.management.commands.check_twilio import Command
        output=StringIO()
        with patch('booking_sms.check_service',return_value={'code_length':6}),patch('booking_sms.send_code') as send:
            Command(stdout=output).handle(send=False,phone=None)
            send.assert_not_called()
        self.assertIn('No SMS sent',output.getvalue())
