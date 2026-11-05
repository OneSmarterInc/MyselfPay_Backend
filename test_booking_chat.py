import os
import unittest
from unittest.mock import patch
import booking
import booking_chat
from urllib.error import HTTPError, URLError
from test_center_portal import CenterPortalTests


class ChatTests(unittest.TestCase):
    setUp = CenterPortalTests.setUp
    tearDown = CenterPortalTests.tearDown
    verify_email = CenterPortalTests.verify_email
    verify_phone = CenterPortalTests.verify_phone
    create = CenterPortalTests.create
    login = CenterPortalTests.login

    def test_retry_diagnostics_do_not_expose_secrets(self):
        errors = (
            (HTTPError('https://example.test/private-token', 403,
                       'patient@example.test secret', {}, None), 'http_rejected'),
            (URLError(TimeoutError('private-token')), 'timeout'),
            (ValueError('private-token'), 'configuration_or_response_invalid'),
        )
        for error, category in errors:
            with self.subTest(category=category):
                with self.assertLogs('booking_chat', level='WARNING') as captured:
                    booking_chat.log_handoff_failure('create_thread', error)
                output = '\n'.join(captured.output)
                self.assertIn(category, output)
                self.assertNotIn('private-token', output)
                self.assertNotIn('patient@example.test', output)
                if isinstance(error, HTTPError):
                    error.close()

    @patch.dict(os.environ, {'SECURE_MAIL_ENABLED':'true'})
    def test_retired_patient_chat_never_queues_or_exposes_a_link(self):
        self.assertFalse(booking_chat.enabled())
        item=self.create()
        with booking.connection(self.db) as con:
            self.assertNotIn('secure_chat_url',booking.receipt(booking.get_row(con,item['id']),con))
            self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM booking_chat').fetchone()['n'],0)

    @patch.dict(os.environ, {'SECURE_MAIL_ENABLED':'true'})
    def test_historical_queued_handoff_is_not_dispatched(self):
        item=self.create()
        with booking.connection(self.db,write=True) as con:
            con.execute('INSERT INTO booking_chat(request_id,created) VALUES(?,0)',(item['id'],))
        with patch('booking_chat.create_thread') as send:
            self.assertFalse(booking_chat.dispatch(self.db,item['id']))
            send.assert_not_called()
        with booking.connection(self.db) as con:
            self.assertEqual(con.execute('SELECT state FROM booking_chat').fetchone()['state'],'queued')

    def test_insecure_remote_origin_is_rejected(self):
        with patch.dict(os.environ,{'SECURE_MAIL_URL':'http://mail.example.test','SECURE_MAIL_ALLOW_LOOPBACK_HTTP':'true'}):
            with self.assertRaises(ValueError): booking_chat.origin('SECURE_MAIL_URL')

    @patch.dict(os.environ, {'SECURE_MAIL_ENABLED':'true', 'BOOKING_EMAIL_ENABLED':'true'})
    def test_mobile_request_notifies_only_the_center(self):
        with patch('booking_mail.send_message') as send,patch('booking_chat.create_thread') as chat:
            item=self.create()
        self.assertEqual(item['delivery'],'sent')
        self.assertNotIn('secure_chat_url',item)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[1],'appointments@example.test')
        self.assertNotIn('patient@example.test',send.call_args.args[0].as_string())
        chat.assert_not_called()

    @patch.dict(os.environ, {'SECURE_MAIL_ENABLED':'true'})
    def test_historical_patient_confirmation_is_not_dispatched(self):
        item=self.create()
        with patch('booking_chat.signed_request') as send:
            self.assertFalse(booking_chat.dispatch_confirmation(self.db,item['id']))
            send.assert_not_called()
