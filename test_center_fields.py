"""Center-only request fields: patient contact in the list, notes, order received, exam completed, response time."""
import unittest
import booking
import center_portal
import test_booking
import test_center_portal


class CenterFieldTests(unittest.TestCase):
    setUp = test_booking.BookingTests.setUp
    tearDown = test_booking.BookingTests.tearDown
    verify_email = test_booking.BookingTests.verify_email
    verify_phone = test_booking.BookingTests.verify_phone
    create = test_booking.BookingTests.create
    login = test_center_portal.CenterPortalTests.login

    def test_list_shows_first_name_and_phone_only_while_a_request_is_live(self):
        result = self.create()
        token, csrf = self.login()
        item = center_portal.dashboard(self.db, token)['requests'][0]
        self.assertTrue(item['first_name'])
        self.assertTrue(item['phone'])
        center_portal.open_request(self.db, token, csrf, result['id'], 'scheduled', {'preferred_index': 0})
        closed = center_portal.dashboard(self.db, token)['requests'][0]
        self.assertNotIn('first_name', closed)
        self.assertNotIn('phone', closed)

    def test_note_and_order_received_are_saved_for_the_center_and_hidden_from_the_patient(self):
        result = self.create()
        token, csrf = self.login()
        saved = center_portal.open_request(self.db, token, csrf, result['id'], note='  Left VM at 2pm  ', order_received=True)
        self.assertEqual(saved['note'], 'Left VM at 2pm')
        self.assertTrue(saved['order_received'])
        item = center_portal.dashboard(self.db, token)['requests'][0]
        self.assertEqual((item['note'], item['order_received']), ('Left VM at 2pm', True))
        patient = booking.patient(self.db, result['id'], result['tracking_token'])
        for key in ('note', 'order_received', 'exam_completed', 'responded', 'center_note'):
            self.assertNotIn(key, patient)

    def test_exam_completed_needs_a_confirmed_time(self):
        result = self.create()
        token, csrf = self.login()
        with self.assertRaisesRegex(booking.BookingError, 'after you confirm'):
            center_portal.open_request(self.db, token, csrf, result['id'], exam_completed='2026-12-01')
        center_portal.open_request(self.db, token, csrf, result['id'], 'scheduled', {'preferred_index': 0})
        done = center_portal.open_request(self.db, token, csrf, result['id'], exam_completed='2026-12-01')
        self.assertEqual(done['exam_completed'], '2026-12-01')
        self.assertEqual(center_portal.dashboard(self.db, token)['requests'][0]['exam_completed'], '2026-12-01')

    def test_median_response_time_counts_first_outcomes_only(self):
        first = self.create()
        token, csrf = self.login()
        self.assertIsNone(center_portal.dashboard(self.db, token)['median_response_seconds'])
        center_portal.open_request(self.db, token, csrf, first['id'], 'voicemail')
        median = center_portal.dashboard(self.db, token)['median_response_seconds']
        self.assertIsNotNone(median)
        self.assertGreaterEqual(median, 0)
        center_portal.open_request(self.db, token, csrf, first['id'], 'scheduled', {'preferred_index': 0})
        again = center_portal.dashboard(self.db, token)['median_response_seconds']
        self.assertAlmostEqual(again, median, places=3)


class CenterRequestInputTests(unittest.TestCase):
    def test_the_request_endpoint_accepts_the_new_fields_and_rejects_bad_ones(self):
        import os
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        os.environ.setdefault('DJANGO_DEBUG', 'true')
        import django
        django.setup()
        from api.serializers import CenterRequestSerializer
        good = CenterRequestSerializer(data={'id': 'MSP-1', 'note': 'Called', 'order_received': True, 'exam_completed': '2026-12-01'})
        self.assertTrue(good.is_valid(), good.errors)
        self.assertEqual(good.validated_data['exam_completed'].isoformat(), '2026-12-01')
        for bad in ({'note': 'x' * 501}, {'exam_completed': 'soon'}, {'order_received': 'maybe'}):
            self.assertFalse(CenterRequestSerializer(data={'id': 'MSP-1', **bad}).is_valid(), bad)


if __name__ == '__main__':
    unittest.main()
