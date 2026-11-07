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
    def test_center_secure_mail_queues_without_exposing_patient_link(self):
        self.assertTrue(booking_chat.enabled())
        item=self.create()
        with booking.connection(self.db) as con:
            self.assertNotIn('secure_chat_url',booking.receipt(booking.get_row(con,item['id']),con))
            self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM booking_chat').fetchone()['n'],1)

    @patch.dict(os.environ, {'SECURE_MAIL_ENABLED':'true'})
    def test_historical_queued_handoff_is_not_dispatched(self):
        item=self.create()
        with booking.connection(self.db,write=True) as con:
            row=booking.get_row(con,item['id']); payload=booking.unpack(row); payload['delivery_route']='center_email'
            con.execute('UPDATE booking_requests SET payload=? WHERE id=?',(booking.pack(payload),item['id']))
        with patch('booking_chat.create_thread') as send:
            self.assertFalse(booking_chat.dispatch(self.db,item['id']))
            send.assert_not_called()
        with booking.connection(self.db) as con:
            self.assertEqual(con.execute('SELECT state FROM booking_chat').fetchone()['state'],'retired')

    def test_insecure_remote_origin_is_rejected(self):
        with patch.dict(os.environ,{'SECURE_MAIL_URL':'http://mail.example.test','SECURE_MAIL_ALLOW_LOOPBACK_HTTP':'true'}):
            with self.assertRaises(ValueError): booking_chat.origin('SECURE_MAIL_URL')

    @patch.dict(os.environ, {'SECURE_MAIL_ENABLED':'true', 'BOOKING_EMAIL_ENABLED':'true'})
    def test_mobile_request_notifies_only_the_center(self):
        with patch('booking_mail.send_message') as send,patch('booking_chat.create_thread') as chat:
            item=self.create()
        self.assertEqual(item['delivery'],'queued')
        self.assertNotIn('secure_chat_url',item)
        send.assert_not_called()
        chat.assert_not_called()
        with booking.connection(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM booking_outbox').fetchone()['n'],0)
            self.assertNotIn('patient_email',booking.unpack(booking.get_row(con,item['id'])))

    @patch.dict(os.environ, {'SECURE_MAIL_ENABLED':'true'})
    def test_historical_patient_confirmation_is_not_dispatched(self):
        item=self.create()
        with patch('booking_chat.signed_request') as send:
            self.assertFalse(booking_chat.dispatch_confirmation(self.db,item['id']))
            send.assert_not_called()

    @patch.dict(os.environ, {'SECURE_MAIL_ENABLED':'true'})
    def test_center_only_payload_retry_and_approval(self):
        import center_portal
        item=self.create()
        result={'id':'12345678-1234-1234-1234-123456789abc','provider_notification':'queued'}
        with patch('booking_chat.signed_request',side_effect=[TimeoutError(),result]) as send:
            self.assertFalse(booking_chat.dispatch(self.db,item['id']))
            with booking.connection(self.db,write=True) as con:
                con.execute('UPDATE booking_chat SET next_attempt=0')
            self.assertTrue(booking_chat.dispatch(self.db,item['id']))
            self.assertFalse(booking_chat.dispatch(self.db,item['id']))
        payload=send.call_args.args[0]
        self.assertEqual(payload['delivery_mode'],'provider_only')
        self.assertEqual(payload['participants'],[{'email':'appointments@example.test','name':'Synthetic Imaging','role':'provider'}])
        self.assertNotIn('patient@example.test',str(payload))
        self.assertIn('/#clinic/',payload['provider_action']['url'])
        self.assertNotIn('secure_chat_url',booking.patient(self.db,item['id'],item['tracking_token']))
        token,csrf=self.login()
        approved=center_portal.open_request(self.db,token,csrf,item['id'],'scheduled',{'preferred_index':0})
        self.assertEqual(approved['status'],'scheduled')
        with booking.connection(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM booking_chat_events').fetchone()['n'],0)
