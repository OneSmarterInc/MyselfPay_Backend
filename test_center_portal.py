"""Center OTP ownership and shared booking status integration."""
import json
import unittest
from unittest.mock import patch
import booking
import center_portal
import core
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
        self.assertNotIn('patient_email', detail)
        with booking.connection(self.db) as db:
            other = {**self.center, 'email':'other@example.test'}
            db.execute('INSERT INTO entries VALUES(?,?,?,?)', ('batch','clinic-2','h2',json.dumps(other)))
        other_token, other_csrf = self.login('other@example.test')
        self.assertEqual(center_portal.dashboard(self.db, other_token)['requests'], [])
        with self.assertRaises(booking.BookingError):
            center_portal.open_request(self.db, other_token, other_csrf, result['id'], 'scheduled', {'preferred_index':0})
        approved = center_portal.open_request(self.db, token, csrf, result['id'], 'scheduled', {'preferred_index':0})
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


class CenterPriceApprovalTests(CenterPriceTests):
    """Approving a price publishes it as a new batch; rejecting changes nothing."""

    def submit(self, amount='450.00'):
        token, csrf = self.login()
        return center_portal.submit_prices(self.db, token, csrf, 'clinic-1', self.prices(amount), True)['saved'][0]['id']

    def test_approve_makes_price_visible_and_keeps_old_batch(self):
        import center_price_apply
        ident = self.submit()
        self.assertEqual(len(center_price_apply.pending(self.db)), 1)
        result = center_price_apply.approve(self.db, ident, 'vikram')
        self.assertEqual(result['previous_batch'], 'batch')
        offers = core.catalog(self.db)['offers']
        self.assertEqual([(o['mod'], o['amount'], o['type']) for o in offers], [('MRI', 450.0, 'fixed')])
        self.assertIn('Price confirmed by center', offers[0]['source'])
        with booking.connection(self.db) as db:
            self.assertEqual(core.active_batch(db), result['batch'])
            self.assertEqual(db.execute("SELECT status,decided_by FROM center_price_submissions WHERE id=?", (ident,)).fetchone()['status'], 'approved')
            self.assertEqual(db.execute("SELECT COUNT(*) AS n FROM offers WHERE batch_id='batch'").fetchone()['n'], 0)
        with self.assertRaises(center_price_apply.PriceError):
            center_price_apply.approve(self.db, ident, 'vikram')

    def test_second_approval_replaces_same_exam_price(self):
        import center_price_apply
        center_price_apply.approve(self.db, self.submit('450'), 'vikram')
        center_price_apply.approve(self.db, self.submit('400'), 'vikram')
        self.assertEqual([o['amount'] for o in core.catalog(self.db)['offers']], [400.0])

    def test_reject_leaves_directory_unchanged(self):
        import center_price_apply
        ident = self.submit()
        center_price_apply.reject(self.db, ident, 'vikram')
        self.assertEqual(core.catalog(self.db)['offers'], [])
        with booking.connection(self.db) as db:
            self.assertEqual(core.active_batch(db), 'batch')
        with self.assertRaises(center_price_apply.PriceError):
            center_price_apply.approve(self.db, ident, 'vikram')

    def test_reviewer_name_required(self):
        import center_price_apply
        with self.assertRaises(center_price_apply.PriceError):
            center_price_apply.approve(self.db, self.submit(), ' ')


class CenterContactTests(unittest.TestCase):
    """Email and phone changes need a code sent to the new value."""
    setUp = test_booking.BookingTests.setUp
    tearDown = test_booking.BookingTests.tearDown
    verify_email = test_booking.BookingTests.verify_email
    verify_phone = test_booking.BookingTests.verify_phone
    create = test_booking.BookingTests.create
    login = CenterPortalTests.login

    def code_from(self, send):
        return send.call_args.args[0].get_body(preferencelist=('plain',)).get_content().split('code is: ')[1].split()[0]

    def test_email_change_needs_code_then_login_moves_to_new_email(self):
        import center_contact
        request = self.create()
        token, csrf = self.login()
        with patch('booking_mail.send_message') as send:
            started = center_contact.start(self.db, token, csrf, 'email', 'New.Owner@Example.test')
        message = send.call_args.args[0]
        self.assertEqual(send.call_args.args[1], 'new.owner@example.test')
        self.assertIn('appointments@example.test', message.get_body(preferencelist=('plain',)).get_content())
        code = message.get_body(preferencelist=('plain',)).get_content().split('code is: ')[1].split()[0]
        bad = '000000' if code != '000000' else '111111'
        with self.assertRaisesRegex(booking.BookingError, 'Incorrect'):
            center_contact.confirm(self.db, token, csrf, started['verification_id'], bad)
        with booking.connection(self.db) as db:
            self.assertEqual(json.loads(db.execute("SELECT payload FROM entries WHERE entry_key='clinic-1' AND batch_id=(SELECT value FROM settings WHERE key='active_batch')").fetchone()['payload'])['email'], 'appointments@example.test')
        with patch('booking_mail.send_message'):
            result = center_contact.confirm(self.db, token, csrf, started['verification_id'], code)
        self.assertTrue(result['login_required'])
        with self.assertRaises(booking.BookingError):
            center_portal.dashboard(self.db, token)
        with patch('booking_mail.send_message'):
            with self.assertRaisesRegex(booking.BookingError, 'contact support'):
                center_portal.issue(self.db, 'appointments@example.test')
        token2, csrf2 = self.login('new.owner@example.test')
        board = center_portal.dashboard(self.db, token2)
        self.assertEqual(board['centers'][0]['email'], 'new.owner@example.test')
        self.assertEqual([r['id'] for r in board['requests']], [request['id']])
        self.assertEqual(center_portal.open_request(self.db, token2, csrf2, request['id'])['id'], request['id'])
        with booking.connection(self.db) as db:
            self.assertEqual(db.execute('SELECT field,old_value,new_value FROM center_contact_changes').fetchone()['new_value'], 'new.owner@example.test')

    def test_email_already_used_by_other_listing_is_refused(self):
        import center_contact
        token, csrf = self.login()
        with booking.connection(self.db) as db:
            db.execute('INSERT INTO entries VALUES(?,?,?,?)', ('batch', 'clinic-2', 'h2', json.dumps({**self.center, 'email': 'other@example.test'})))
        with patch('booking_mail.send_message') as send:
            with self.assertRaisesRegex(booking.BookingError, 'another center'):
                center_contact.start(self.db, token, csrf, 'email', 'other@example.test')
            send.assert_not_called()

    def test_code_is_bound_to_session_and_csrf_is_checked(self):
        import center_contact
        token, csrf = self.login()
        with patch('booking_mail.send_message') as send:
            with self.assertRaisesRegex(booking.BookingError, 'refresh'):
                center_contact.start(self.db, token, 'wrong', 'email', 'new.owner@example.test')
            started = center_contact.start(self.db, token, csrf, 'email', 'new.owner@example.test')
        code = self.code_from(send)
        with booking.connection(self.db) as db:
            db.execute('INSERT INTO entries VALUES(?,?,?,?)', ('batch', 'clinic-2', 'h2', json.dumps({**self.center, 'email': 'other@example.test'})))
        other, other_csrf = self.login('other@example.test')
        with self.assertRaisesRegex(booking.BookingError, 'expired or already used'):
            center_contact.confirm(self.db, other, other_csrf, started['verification_id'], code)

    def test_phone_change_updates_only_that_listing_after_sms_code(self):
        import center_contact
        token, csrf = self.login()
        with patch('booking_sms.send_code', return_value='VE' + '1' * 32) as send:
            started = center_contact.start(self.db, token, csrf, 'phone', '+1 937 555 0142', 'clinic-1')
        send.assert_called_once_with('+19375550142')
        with patch('booking_sms.check_code', return_value=False):
            with self.assertRaisesRegex(booking.BookingError, 'Incorrect'):
                center_contact.confirm(self.db, token, csrf, started['verification_id'], '123456')
        with patch('booking_sms.check_code', return_value=True):
            result = center_contact.confirm(self.db, token, csrf, started['verification_id'], '123456')
        self.assertEqual(result['phone'], '+19375550142')
        self.assertEqual(center_portal.dashboard(self.db, token)['centers'][0]['phone'], '+19375550142')

    def test_send_limit(self):
        import center_contact
        token, csrf = self.login()
        with patch('booking_mail.send_message'):
            for n in range(center_contact.SEND_LIMIT):
                center_contact.start(self.db, token, csrf, 'email', f'n{n}@example.test')
            with self.assertRaisesRegex(booking.BookingError, 'Too many'):
                center_contact.start(self.db, token, csrf, 'email', 'n9@example.test')

    def test_live_price_is_shown_to_center_after_approval(self):
        import center_price_apply
        token, csrf = self.login()
        ident = center_portal.submit_prices(self.db, token, csrf, 'clinic-1',
            [{'exam': 'MRI', 'amount': '450', 'price_type': 'fixed', 'contrast': 'without', 'interpretation_included': 'yes', 'note': ''}], True)['saved'][0]['id']
        self.assertEqual(center_portal.dashboard(self.db, token)['live_prices'], [])
        center_price_apply.approve(self.db, ident, 'vikram')
        live = center_portal.dashboard(self.db, token)['live_prices']
        self.assertEqual([(p['exam'], p['amount_cents'], p['contrast'], p['interpretation_included'], p['price_type']) for p in live], [('MRI', 45000, 'without', 'yes', 'fixed')])


    def test_history_lists_contact_changes_and_price_decisions(self):
        import center_contact
        token, csrf = self.login()
        with patch('booking_sms.send_code', return_value='VE' + '1' * 32):
            started = center_contact.start(self.db, token, csrf, 'phone', '+19375550142', 'clinic-1')
        with patch('booking_sms.check_code', return_value=True):
            center_contact.confirm(self.db, token, csrf, started['verification_id'], '123456')
        board = center_portal.dashboard(self.db, token)
        self.assertEqual([(h['field'], h['new_value']) for h in board['contact_history']], [('phone', '+19375550142')])
        self.assertTrue(board['contact_history'][0]['changed'] > 0)


if __name__ == '__main__':
    unittest.main()
