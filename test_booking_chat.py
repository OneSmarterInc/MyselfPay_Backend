import os
import unittest
from unittest.mock import patch
import booking
import booking_chat
from test_center_portal import CenterPortalTests


class ChatTests(unittest.TestCase):
    setUp = CenterPortalTests.setUp
    tearDown = CenterPortalTests.tearDown
    verify_email = CenterPortalTests.verify_email
    verify_phone = CenterPortalTests.verify_phone
    create = CenterPortalTests.create
    login = CenterPortalTests.login

    @patch.dict(os.environ, {'SECURE_MAIL_ENABLED':'true', 'SECURE_MAIL_PORTAL_URL':'https://mail.example.test', 'SECURE_MAIL_KEY_ID':'selfpay'})
    def test_submission_queues_chat_and_delivery_is_idempotent(self):
        import center_portal
        item=self.create()
        with booking.connection(self.db) as con:
            self.assertNotIn('secure_chat_url',booking.receipt(booking.get_row(con,item['id']),con))
            self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM booking_chat').fetchone()['n'],1)
        token,csrf=self.login()
        center_portal.open_request(self.db,token,csrf,item['id'],'scheduled')
        center_portal.open_request(self.db,token,csrf,item['id'],'scheduled')
        with patch('booking_chat.create_thread',return_value='12345678-1234-1234-1234-123456789abc') as send:
            self.assertTrue(booking_chat.dispatch(self.db,item['id']))
            self.assertFalse(booking_chat.dispatch(self.db,item['id']))
            send.assert_called_once()
        with booking.connection(self.db) as con:
            receipt=booking.receipt(booking.get_row(con,item['id']),con)
            self.assertTrue(receipt['secure_chat_url'].startswith('https://mail.example.test/recipient/inbox'))
            self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM booking_chat').fetchone()['n'],1)

    @patch.dict(os.environ, {'SECURE_MAIL_ENABLED':'true'})
    def test_failure_remains_retryable_without_undoing_approval(self):
        import center_portal
        item=self.create()
        token,csrf=self.login()
        center_portal.open_request(self.db,token,csrf,item['id'],'scheduled')
        with patch('booking_chat.create_thread',side_effect=TimeoutError):
            self.assertFalse(booking_chat.dispatch(self.db,item['id']))
        with booking.connection(self.db) as con:
            self.assertEqual(booking.get_row(con,item['id'])['status'],'scheduled')
            self.assertEqual(con.execute('SELECT state FROM booking_chat').fetchone()['state'],'retry')

    def test_insecure_remote_origin_is_rejected(self):
        with patch.dict(os.environ,{'SECURE_MAIL_URL':'http://mail.example.test','SECURE_MAIL_ALLOW_LOOPBACK_HTTP':'true'}):
            with self.assertRaises(ValueError): booking_chat.origin('SECURE_MAIL_URL')

    @patch.dict(os.environ, {'SECURE_MAIL_ENABLED':'true', 'BOOKING_EMAIL_ENABLED':'true'})
    def test_submission_never_sends_direct_clinic_email(self):
        import booking_delivery
        with patch('booking_mail.send_message') as smtp:
            item=self.create()
            self.assertEqual(item['status'],'requested')
            self.assertEqual(item['secure_chat_status'],'pending')
            with booking.connection(self.db,write=True) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM booking_outbox').fetchone()['n'],0)
                booking_delivery.enqueue(con,item['id'])
            self.assertFalse(booking_delivery.deliver(self.db,item['id']))
            smtp.assert_not_called()
        with patch('booking_chat.create_thread',return_value='12345678-1234-1234-1234-123456789abc'):
            self.assertTrue(booking_chat.dispatch(self.db,item['id']))
