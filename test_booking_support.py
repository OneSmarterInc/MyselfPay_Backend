"""Support routing uses synthetic requests and mocked delivery only."""
import json
import os
import unittest
from unittest.mock import patch

import booking
import booking_chat
import test_booking


class SupportRoutingTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_booking.BookingTests()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def center(self, **values):
        center = {'name': 'Synthetic no-contact center', 'area': 'Test city', **values}
        with booking.connection(self.fixture.db, write=True) as con:
            con.execute('UPDATE entries SET payload=? WHERE entry_key=?', (json.dumps(center), 'clinic-1'))
        return center

    def test_missing_or_ineligible_contact_routes_to_support(self):
        for values in ({}, {'email': 'billing@example.test', 'email_scope': 'Billing'},
                       {'email': 'invalid', 'appointment_email_approved': True}):
            center = self.center(**values)
            self.assertEqual(booking.request_recipient(center), 'patientcare@onesmarterhealthweb.com')
        options = booking.options(self.fixture.db, 'clinic-1')
        self.assertEqual(options['notification_target'], 'support')
        self.assertNotEqual(options['delivery'], 'unavailable')

    @patch.dict(os.environ, {'SECURE_MAIL_ENABLED': 'true'})
    def test_secure_support_request_is_durable_idempotent_and_approved_after_otp(self):
        self.center()
        with patch('booking_mail.send_message') as direct_mail:
            item = self.fixture.create({**self.fixture.data, 'recipient': 'attacker@example.test'})
            self.assertEqual(self.fixture.create()['id'], item['id'])
            self.assertEqual(item['notification_target'], 'support')
            self.assertEqual(item['delivery'], 'queued')
            with patch('booking_chat.signed_request', return_value={
                'id': '14b33126-1003-4d8a-9119-69b1dfbfaaaf', 'provider_notification': 'queued'
            }) as handoff:
                self.assertTrue(booking_chat.dispatch(self.fixture.db, item['id']))
            data = handoff.call_args.args[0]
            self.assertEqual(data['delivery_mode'], 'provider_only')
            self.assertEqual(len(data['participants']), 1)
            self.assertEqual(data['participants'][0]['email'], 'patientcare@onesmarterhealthweb.com')
            self.assertIn('Support follow-up', data['body'])
            self.assertIn('Synthetic no-contact center', data['body'])
            direct_mail.assert_not_called()
        session = self.fixture.authorize_clinic(item)
        booking.clinic_details(self.fixture.db, item['id'], session, 'scheduled')
        receipt = booking.patient(self.fixture.db, item['id'], item['tracking_token'])
        self.assertEqual(receipt['status'], 'scheduled')

    def test_existing_center_recipient_is_preserved_and_smtp_fallback_is_scoped(self):
        self.assertEqual(booking.request_recipient(self.fixture.center), 'appointments@example.test')
        self.center()
        with patch.dict(os.environ, {'BOOKING_EMAIL_ENABLED': 'true', 'BOOKING_EMAIL_ALLOWED_RECIPIENTS': ''}), \
                patch('booking_mail.send_message') as send:
            item = self.fixture.create()
            self.assertEqual(item['delivery'], 'sent')
            self.assertEqual(send.call_args.args[1], 'patientcare@onesmarterhealthweb.com')
        with booking.connection(self.fixture.db) as con:
            payload = booking.unpack(booking.get_row(con, item['id']))
            self.assertEqual(payload['clinic_name'], 'Synthetic no-contact center')


if __name__ == '__main__':
    unittest.main()
