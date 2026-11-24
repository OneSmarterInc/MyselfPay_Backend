import json
import os
import unittest
from unittest.mock import patch,MagicMock
from email.message import EmailMessage
import securemail_api

class PublicApiTests(unittest.TestCase):
    @patch.dict(os.environ,{'SECUREMAIL_BASE_URL':'https://securemailbackend.onesmarter.com/api/v1','SECUREMAIL_API_KEY':'synthetic-key','SECUREMAIL_SENDER':'sender@example.test'})
    def test_bearer_idempotency_and_plain_content(self):
        message=EmailMessage();message['Subject']='Sign in';message.set_content('Synthetic sign-in link')
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"id":"test-id"}'
        opener=MagicMock();opener.open.return_value=response
        with patch('securemail_api.build_opener',return_value=opener):
            securemail_api.send_message(message,'recipient@example.test')
            first=opener.open.call_args.args[0]
            securemail_api.send_message(message,'recipient@example.test')
            second=opener.open.call_args.args[0]
        self.assertEqual(first.full_url,'https://securemailbackend.onesmarter.com/api/v1/messages')
        self.assertEqual(first.get_header('Authorization'),'Bearer synthetic-key')
        self.assertEqual(first.get_header('Idempotency-key'),second.get_header('Idempotency-key'))
        self.assertFalse(json.loads(first.data)['reply'])
    @patch.dict(os.environ,{'SECUREMAIL_BASE_URL':'http://example.test/api/v1','SECUREMAIL_API_KEY':'synthetic'})
    def test_http_rejected(self):
        with self.assertRaises(ValueError):securemail_api.request('/status')
    def test_redirect_rejected(self):
        with self.assertRaises(ValueError):securemail_api.NoRedirect().redirect_request(None,None,302,'',None,'https://other.test')
    @patch.dict(os.environ,{'SECURE_MAIL_TRANSPORT':'api'})
    def test_booking_uses_only_public_transport(self):
        import booking_chat
        payload={'slots':[{'date':'2026-12-01','period':'morning'}],'clinic_name':'Test clinic','first_name':'Synthetic','timezone':'America/New_York','price_text':'Quote required','clinic_url':'https://myselfpay.onesmarter.com/#clinic/test','clinic_link_expires':1900000000,'recipient':'clinic@example.test'}
        with patch('securemail_api.request',return_value={'id':'12345678-1234-1234-1234-123456789012','provider_notification':'queued'}) as send,patch('booking_chat.signed_request') as legacy:
            booking_chat.create_thread('test-request',payload)
        legacy.assert_not_called();self.assertEqual(send.call_args.args[0],'/bookings')

if __name__=='__main__':unittest.main()
