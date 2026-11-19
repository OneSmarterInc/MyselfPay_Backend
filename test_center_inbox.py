"""Request inbox: a center sends itself a test, enters the code, and new requests arrive there."""
import unittest
from unittest.mock import patch
import booking
import booking_delivery
import booking_mail
import center_inbox
import center_portal
import test_booking
import test_center_portal


def code_from(send):
    return send.call_args.args[0].get_body(preferencelist=('plain',)).get_content().split('code is: ')[1].split()[0]


class CenterInboxTests(unittest.TestCase):
    setUp = test_booking.BookingTests.setUp
    tearDown = test_booking.BookingTests.tearDown
    verify_email = test_booking.BookingTests.verify_email
    verify_phone = test_booking.BookingTests.verify_phone
    create = test_booking.BookingTests.create
    login = test_center_portal.CenterPortalTests.login

    def set_inbox(self, token, csrf, address='front-desk@example.test'):
        with patch('booking_mail.send_message') as send:
            started = center_inbox.start(self.db, token, csrf, address)
        self.assertEqual(send.call_args.args[1], address)
        return started, code_from(send)

    def test_no_inbox_until_the_code_is_entered(self):
        token, csrf = self.login()
        started, code = self.set_inbox(token, csrf)
        self.assertEqual(center_portal.dashboard(self.db, token)['request_inbox'], '')
        with self.assertRaisesRegex(booking.BookingError, 'Incorrect'):
            center_inbox.confirm(self.db, token, csrf, started['verification_id'], '000000' if code != '000000' else '111111')
        self.assertEqual(center_portal.dashboard(self.db, token)['request_inbox'], '')
        center_inbox.confirm(self.db, token, csrf, started['verification_id'], code)
        self.assertEqual(center_portal.dashboard(self.db, token)['request_inbox'], 'front-desk@example.test')

    def test_new_requests_go_to_the_inbox_and_stay_owned_by_the_login_email(self):
        token, csrf = self.login()
        started, code = self.set_inbox(token, csrf)
        center_inbox.confirm(self.db, token, csrf, started['verification_id'], code)
        result = self.create()
        with booking.connection(self.db) as db:
            payload = booking.unpack(booking.get_row(db, result['id']))
        self.assertEqual(payload['recipient'], 'appointments@example.test')
        self.assertEqual(booking.delivery_address(payload), 'front-desk@example.test')
        self.assertEqual(len(center_portal.dashboard(self.db, token)['requests']), 1)
        self.assertEqual(booking.notification(payload, result['id'])['To'], 'front-desk@example.test')

    def test_removing_the_inbox_returns_delivery_to_the_listing_email(self):
        token, csrf = self.login()
        started, code = self.set_inbox(token, csrf)
        center_inbox.confirm(self.db, token, csrf, started['verification_id'], code)
        center_inbox.remove(self.db, token, csrf)
        result = self.create()
        with booking.connection(self.db) as db:
            payload = booking.unpack(booking.get_row(db, result['id']))
        self.assertEqual(booking.delivery_address(payload), 'appointments@example.test')

    def test_code_belongs_to_the_session_and_expires_after_five_wrong_tries(self):
        token, csrf = self.login()
        started, code = self.set_inbox(token, csrf)
        other, other_csrf = self.login()
        with self.assertRaisesRegex(booking.BookingError, 'expired'):
            center_inbox.confirm(self.db, other, other_csrf, started['verification_id'], code)
        wrong = '000000' if code != '000000' else '111111'
        for _ in range(5):
            with self.assertRaises(booking.BookingError):
                center_inbox.confirm(self.db, token, csrf, started['verification_id'], wrong)
        with self.assertRaisesRegex(booking.BookingError, 'Too many'):
            center_inbox.confirm(self.db, token, csrf, started['verification_id'], code)

    def test_rejects_bad_addresses_and_the_login_email_and_limits_tests(self):
        token, csrf = self.login()
        for bad in ('', 'not-an-email', 'appointments@example.test'):
            with self.assertRaises(booking.BookingError):
                center_inbox.start(self.db, token, csrf, bad)
        with patch('booking_mail.send_message'):
            for _ in range(center_inbox.SEND_LIMIT):
                center_inbox.start(self.db, token, csrf, 'front-desk@example.test')
            with self.assertRaisesRegex(booking.BookingError, 'Too many'):
                center_inbox.start(self.db, token, csrf, 'front-desk@example.test')

    def test_mail_failure_leaves_nothing_pending(self):
        token, csrf = self.login()
        with patch('booking_mail.send_message', side_effect=booking_mail.MailUnavailable('SMTP unavailable')):
            with self.assertRaises(booking.BookingError):
                center_inbox.start(self.db, token, csrf, 'front-desk@example.test')
        with booking.connection(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM center_inbox_checks').fetchone()['n'], 0)

    def test_requires_a_center_session(self):
        with self.assertRaises(booking.BookingError):
            center_inbox.start(self.db, 'bogus', 'bogus', 'front-desk@example.test')


if __name__ == '__main__':
    unittest.main()
