"""A clinic booking must not fall back to direct SelfPay email."""
import os
import unittest
from unittest.mock import patch

import booking
import booking_chat
from test_center_portal import CenterPortalTests


class SecureMailOnlyTests(unittest.TestCase):
    setUp = CenterPortalTests.setUp
    tearDown = CenterPortalTests.tearDown
    verify_email = CenterPortalTests.verify_email
    verify_phone = CenterPortalTests.verify_phone
    create = CenterPortalTests.create

    @patch.dict(os.environ, {'SECURE_MAIL_ENABLED': 'true', 'BOOKING_EMAIL_ENABLED': 'true'})
    def test_outage_never_falls_back_to_selfpay_email(self):
        with patch('booking_mail.send_message') as direct_mail:
            item = self.create()
            with patch('booking_chat.create_thread', side_effect=TimeoutError()):
                self.assertFalse(booking_chat.dispatch(self.db, item['id']))
            direct_mail.assert_not_called()
        with booking.connection(self.db) as con:
            count = con.execute('SELECT COUNT(*) AS n FROM booking_outbox').fetchone()['n']
            self.assertEqual(count, 0)
            job = con.execute(
                'SELECT state,thread_id FROM booking_chat WHERE request_id=?', (item['id'],)
            ).fetchone()
            self.assertEqual(job['state'], 'retry')
            self.assertIsNone(job['thread_id'])
