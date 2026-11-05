import unittest
import json
from unittest.mock import patch
import booking
import core
import test_booking
from mail_diagnostics import request_status


class MailDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_booking.BookingTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_suppressed_request_is_read_only_and_never_exposes_tokens(self):
        item = self.fixture.create()
        with patch('booking_mail.send_message') as send:
            info = request_status(self.fixture.db, item['id'])
        send.assert_not_called()
        self.assertEqual(info['recipient'], 'appointments@example.test')
        self.assertEqual(info['state'], 'suppressed')
        self.assertNotIn(item['tracking_token'], str(info))
        self.assertNotIn('patient@example.test', str(info))

    def test_historical_center_and_pending_secure_mail_remain_diagnosable(self):
        item = self.fixture.create()
        with booking.connection(self.fixture.db, write=True) as db:
            db.execute("INSERT INTO booking_chat(request_id,created) VALUES(?,0)", (item['id'],))
            db.execute("DELETE FROM entries WHERE entry_key='clinic-1'")
        info = request_status(self.fixture.db, item['id'])
        self.assertEqual(info['route'], 'Secure Mail')
        self.assertEqual(info['state'], 'queued')
        self.assertIn('not in the current', ' '.join(info['notes']))
        self.assertIn('currently disabled', ' '.join(info['notes']))

    @patch.dict('os.environ', {'SECURE_MAIL_ENABLED':'true'})
    def test_mobile_only_booking_does_not_need_distinct_patient_email(self):
        center = {**self.fixture.center, 'email': 'patient@example.test'}
        with booking.connection(self.fixture.db, write=True) as db:
            db.execute('UPDATE entries SET payload=? WHERE entry_key=?', (json.dumps(center), 'clinic-1'))
        self.assertTrue(self.fixture.create()['id'])
        with booking.connection(self.fixture.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM booking_chat').fetchone()['n'], 0)
