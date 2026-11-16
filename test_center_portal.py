"""Center OTP ownership and shared booking status integration."""
import json
import unittest
from unittest.mock import patch
import booking
import center_portal
import test_booking


class CenterPortalTests(unittest.TestCase):
    setUp = test_booking.BookingTests.setUp
    tearDown = test_booking.BookingTests.tearDown
    verify_email = test_booking.BookingTests.verify_email
    verify_phone = test_booking.BookingTests.verify_phone
    create = test_booking.BookingTests.create

    def login(self, email='appointments@example.test'):
        with patch('booking_mail.send_message') as send:
            issued = center_portal.issue(self.db, email)
        code = send.call_args.args[0].get_body(preferencelist=('plain',)).get_content().split('code is: ')[1].split()[0]
        token, csrf = center_portal.verify(self.db, issued['verification_id'], code)
        return token, csrf

    def test_unknown_email_never_sends(self):
        with patch('booking_mail.send_message') as send:
            with self.assertRaisesRegex(booking.BookingError, 'contact support'):
                center_portal.issue(self.db, 'absent@example.test')
            send.assert_not_called()

    def test_otp_is_single_use_and_attempts_persist(self):
        with patch('booking_mail.send_message') as send:
            issued = center_portal.issue(self.db, 'APPOINTMENTS@example.test')
        code = send.call_args.args[0].get_body(preferencelist=('plain',)).get_content().split('code is: ')[1].split()[0]
        bad = '000000' if code != '000000' else '111111'
        with self.assertRaisesRegex(booking.BookingError, 'Incorrect'):
            center_portal.verify(self.db, issued['verification_id'], bad)
        with booking.connection(self.db) as db:
            self.assertEqual(db.execute('SELECT attempts FROM center_login_checks WHERE id=?', (issued['verification_id'],)).fetchone()['attempts'], 1)
        center_portal.verify(self.db, issued['verification_id'], code)
        with self.assertRaisesRegex(booking.BookingError, 'already used'):
            center_portal.verify(self.db, issued['verification_id'], code)

    def test_dashboard_scoping_approval_and_revocation(self):
        result = self.create()
        token, csrf = self.login()
        dashboard = center_portal.dashboard(self.db, token)
        self.assertEqual([r['id'] for r in dashboard['requests']], [result['id']])
        self.assertNotIn('tracking_token', dashboard['requests'][0])
        self.assertNotIn('patient_email', dashboard['requests'][0])
        with self.assertRaises(booking.BookingError):
            center_portal.open_request(self.db, token, 'bad-csrf', result['id'])
        detail = center_portal.open_request(self.db, token, csrf, result['id'])
        self.assertEqual(detail['patient_email'], 'patient@example.test')
        with booking.connection(self.db) as db:
            other = {**self.center, 'email':'other@example.test'}
            db.execute('INSERT INTO entries VALUES(?,?,?,?)', ('batch','clinic-2','h2',json.dumps(other)))
        other_token, other_csrf = self.login('other@example.test')
        self.assertEqual(center_portal.dashboard(self.db, other_token)['requests'], [])
        with self.assertRaises(booking.BookingError):
            center_portal.open_request(self.db, other_token, other_csrf, result['id'], 'scheduled')
        approved = center_portal.open_request(self.db, token, csrf, result['id'], 'scheduled')
        self.assertEqual(approved['status'], 'scheduled')
        self.assertTrue(approved['contact_hidden'])
        self.assertEqual(booking.patient(self.db, result['id'], result['tracking_token'])['status'], 'scheduled')
        with booking.connection(self.db) as db:
            self.assertEqual(booking.get_row(db, result['id'])['status'], 'scheduled')
            db.execute('UPDATE entries SET payload=? WHERE entry_key=?', (json.dumps(other),'clinic-1'))
        with self.assertRaisesRegex(booking.BookingError, 'no longer listed'):
            center_portal.dashboard(self.db, token)

    def test_mail_failure_invalidates_challenge(self):
        import booking_mail
        with patch('booking_mail.send_message', side_effect=booking_mail.MailUnavailable('SMTP unavailable')):
            with self.assertRaisesRegex(booking.BookingError, 'SMTP unavailable'):
                center_portal.issue(self.db,'appointments@example.test')
        with booking.connection(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) n FROM center_login_checks').fetchone()['n'],0)


if __name__ == '__main__':
    unittest.main()


class CenterPriceTests(unittest.TestCase):
    """Center-entered prices are stored for review and never change the directory."""
    setUp = test_booking.BookingTests.setUp
    tearDown = test_booking.BookingTests.tearDown
    verify_email = test_booking.BookingTests.verify_email
    verify_phone = test_booking.BookingTests.verify_phone
    login = CenterPortalTests.login

    def prices(self, amount='450.00'):
        return [{'exam': 'MRI', 'amount': amount, 'price_type': 'fixed', 'contrast': 'without',
                 'interpretation_included': 'yes', 'note': ''}]

    def test_submission_is_pending_and_directory_unchanged(self):
        token, csrf = self.login()
        with booking.connection(self.db) as db:
            before = db.execute("SELECT payload FROM entries WHERE entry_key='clinic-1'").fetchone()['payload']
        result = center_portal.submit_prices(self.db, token, csrf, 'clinic-1', self.prices(), True)
        self.assertEqual(result['saved'][0]['status'], 'pending')
        self.assertEqual(result['saved'][0]['amount_cents'], 45000)
        with booking.connection(self.db) as db:
            self.assertEqual(db.execute("SELECT payload FROM entries WHERE entry_key='clinic-1'").fetchone()['payload'], before)
        board = center_portal.dashboard(self.db, token)
        self.assertEqual(board['price_submissions'][0]['status'], 'pending')

    def test_consent_csrf_and_listing_are_enforced(self):
        token, csrf = self.login()
        with self.assertRaisesRegex(booking.BookingError, 'Confirm'):
            center_portal.submit_prices(self.db, token, csrf, 'clinic-1', self.prices(), False)
        with self.assertRaisesRegex(booking.BookingError, 'refresh'):
            center_portal.submit_prices(self.db, token, 'wrong', 'clinic-1', self.prices(), True)
        with self.assertRaisesRegex(booking.BookingError, 'unavailable'):
            center_portal.submit_prices(self.db, token, csrf, 'other-clinic', self.prices(), True)
        with booking.connection(self.db) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) AS n FROM center_price_submissions').fetchone()['n'], 0)

    def test_new_submission_replaces_pending_one(self):
        token, csrf = self.login()
        center_portal.submit_prices(self.db, token, csrf, 'clinic-1', self.prices('400'), True)
        center_portal.submit_prices(self.db, token, csrf, 'clinic-1', self.prices('425'), True)
        with booking.connection(self.db) as db:
            rows = db.execute("SELECT amount_cents,status FROM center_price_submissions ORDER BY submitted").fetchall()
        self.assertEqual([(r['amount_cents'], r['status']) for r in rows], [(40000, 'replaced'), (42500, 'pending')])
