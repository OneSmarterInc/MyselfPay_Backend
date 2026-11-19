"""Request rules: limits, cancel, auto-close, expiry, center login and Forget me. Synthetic data only."""
from datetime import datetime, timedelta
import time
import unittest
from unittest.mock import patch
import account_forget
import appointment_slots
import booking
import center_portal
import core
import test_booking
from test_center_portal import CenterPortalTests


class RequestRuleTests(unittest.TestCase):
    setUp = test_booking.BookingTests.setUp
    tearDown = test_booking.BookingTests.tearDown
    verify_email = test_booking.BookingTests.verify_email
    verify_phone = test_booking.BookingTests.verify_phone
    tokens = test_booking.BookingTests.tokens
    authorize_clinic = test_booking.BookingTests.authorize_clinic
    login = CenterPortalTests.login

    def add_center(self, key, name='Second Imaging'):
        center = {**self.center, 'name': name, 'email': key + '@example.test'}
        with booking.connection(self.db) as con:
            con.execute('INSERT INTO entries VALUES(?,?,?,?)', ('batch', key, 'h', __import__('json').dumps(center)))

    def make(self, clinic='clinic-1', key='k', **extra):
        data = {**self.data, 'clinic_id': clinic, 'idempotency_key': key, **extra}
        return booking.create(self.db, 'guest-owner', data, 'http://127.0.0.1:8765')

    def test_a_request_lasts_seven_days(self):
        item = self.make()
        with booking.connection(self.db) as con:
            row = booking.get_row(con, item['id'])
        self.assertAlmostEqual(row['link_expires'] - row['created'], 7 * 86400, delta=5)

    def test_one_open_request_per_facility_and_exam(self):
        self.make(key='one')
        with self.assertRaisesRegex(booking.BookingError, 'already asked') as ctx:
            self.make(key='two')
        self.assertEqual(ctx.exception.status, 409)
        self.make(key='three', exam='Shoulder')  # a different exam is allowed

    def test_three_open_requests_at_most(self):
        for n in range(3):
            self.add_center(f'c{n}', f'Imaging {n}')
            self.make(clinic=f'c{n}', key=f'k{n}')
        self.add_center('c9', 'Imaging 9')
        with self.assertRaisesRegex(booking.BookingError, '3 open requests'):
            self.make(clinic='c9', key='k9')

    def test_patient_can_cancel_and_a_slot_opens_up(self):
        for n in range(3):
            self.add_center(f'c{n}', f'Imaging {n}')
            last = self.make(clinic=f'c{n}', key=f'k{n}')
        result = booking.cancel(self.db, 'guest-owner', last['id'])
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['closed_reason'], 'patient_cancelled')
        self.add_center('c9', 'Imaging 9')
        self.make(clinic='c9', key='k9')
        with self.assertRaises(booking.BookingError):
            booking.cancel(self.db, 'someone-else', last['id'])

    def test_confirming_one_closes_the_others_for_the_same_exam(self):
        self.add_center('c2', 'Imaging 2')
        first = self.make(clinic='clinic-1', key='a')
        second = self.make(clinic='c2', key='b')
        other_exam = self.make(clinic='c2', key='c', exam='Shoulder')
        session = self.authorize_clinic(first)
        with patch('booking_mail.send_message'):
            booking.clinic_details(self.db, first['id'], session, 'scheduled', {'preferred_index': 0})
        with booking.connection(self.db) as con:
            closed = booking.get_row(con, second['id'])
            kept = booking.get_row(con, other_exam['id'])
            self.assertEqual(closed['status'], 'cancelled')
            self.assertEqual(booking.unpack(closed)['closed_reason'], 'scheduled_elsewhere')
            self.assertEqual(kept['status'], 'requested')

    def test_closed_facilities_are_told_by_email(self):
        self.add_center('c2', 'Imaging 2')
        first = self.make(clinic='clinic-1', key='a')
        second = self.make(clinic='c2', key='b')
        session = self.authorize_clinic(first)
        with patch('booking_delivery.mode', return_value='enabled'), patch('booking_mail.send_message') as send:
            # Requests made in these tests route by center email.
            booking.clinic_details(self.db, first['id'], session, 'scheduled', {'preferred_index': 0})
        self.assertTrue(send.called)
        message = send.call_args.args[0]
        self.assertIn('another facility', message.get_content())
        self.assertNotIn('Synthetic Jamie', message.get_content())

    def test_unable_keeps_a_reason(self):
        item = self.make()
        session = self.authorize_clinic(item)
        result = booking.clinic_details(self.db, item['id'], session, 'unable', None, 'no_openings')
        self.assertEqual(result['closed_reason'], 'no_openings')
        with self.assertRaises(booking.BookingError):
            other = self.make(key='z', exam='Hip')
            booking.clinic_details(self.db, other['id'], self.authorize_clinic(other), 'unable', None, 'bogus')

    def test_center_login_answers_the_same_for_every_email(self):
        with patch('booking_mail.send_message') as send:
            unknown = center_portal.issue(self.db, 'absent@example.test')
            send.assert_not_called()
        self.assertEqual(set(unknown), {'verification_id', 'expires_in'})
        with self.assertRaises(booking.BookingError):
            center_portal.verify(self.db, unknown['verification_id'], '123456')
        with patch('booking_mail.send_message'):
            known = center_portal.issue(self.db, 'appointments@example.test')
        self.assertEqual(set(known), set(unknown))

    def test_forget_me_deletes_the_account_and_tells_facilities(self):
        item = self.make()
        with patch('booking_sms.configuration', return_value={}), patch('booking_sms.send_code', return_value='VE' + '1' * 32):
            issued = account_forget.issue(self.db, 'guest-owner')
        with patch('booking_sms.check_code', return_value=True), patch('booking_delivery.mode', return_value='enabled'), patch('booking_mail.send_message') as send:
            result = account_forget.forget(self.db, 'guest-owner', issued['verification_id'], '123456')
        self.assertEqual(result, {'deleted': True, 'requests_cancelled': 1})
        self.assertTrue(send.called)
        with core.connect(self.db) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM members WHERE id='guest-owner'").fetchone())
            self.assertIsNone(db.execute("SELECT 1 FROM mobile_accounts WHERE member_id='guest-owner'").fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM booking_requests').fetchone()['n'], 0)

    def test_forget_me_needs_the_right_code(self):
        with patch('booking_sms.configuration', return_value={}), patch('booking_sms.send_code', return_value='VE' + '1' * 32):
            issued = account_forget.issue(self.db, 'guest-owner')
        with patch('booking_sms.check_code', return_value=False):
            with self.assertRaises(booking.BookingError):
                account_forget.forget(self.db, 'guest-owner', issued['verification_id'], '000000')
        with core.connect(self.db) as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM members WHERE id='guest-owner'").fetchone())

    def test_account_goes_30_days_after_the_last_request_closes(self):
        item = self.make()
        booking.cancel(self.db, 'guest-owner', item['id'])
        self.assertEqual(account_forget.purge_inactive(self.db), 0)
        later = time.time() + 31 * 86400
        self.assertEqual(account_forget.purge_inactive(self.db, now=later), 1)
        with core.connect(self.db) as db:
            self.assertIsNone(db.execute("SELECT 1 FROM members WHERE id='guest-owner'").fetchone())

    def test_an_account_with_an_open_request_is_kept(self):
        self.make()
        self.assertEqual(account_forget.purge_inactive(self.db, now=time.time() + 6 * 86400), 0)


if __name__ == '__main__':
    unittest.main()
