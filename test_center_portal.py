"""Center OTP ownership and shared booking status integration."""
import json
import os
import sqlite3
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
        self.assertGreater(live[0]['updated'], 0)
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

    def test_center_confirms_scraped_price(self):
        import center_price_apply, center_profile, core
        token, csrf = self.login()
        ident = center_portal.submit_prices(self.db, token, csrf, 'clinic-1',
            [{'exam': 'MRI', 'amount': '450', 'price_type': 'fixed', 'contrast': 'without', 'interpretation_included': 'yes', 'note': ''}], True)['saved'][0]['id']
        center_price_apply.approve(self.db, ident, 'vikram')
        offer_id = 'center-' + ident
        with core.connect(self.db) as db:
            row = db.execute('SELECT payload FROM offers WHERE batch_id=? AND id=?', (core.active_batch(db), offer_id)).fetchone()
            data = json.loads(row['payload'])
            data.pop('confirmed_by_center')
            db.execute('UPDATE offers SET payload=? WHERE batch_id=? AND id=?', (core.canonical(data), core.active_batch(db), offer_id))
        self.assertEqual(center_portal.dashboard(self.db, token)['live_prices'][0]['confirmed'], '')
        with self.assertRaises(booking.BookingError):
            center_profile.confirm_price(self.db, token, csrf, 'missing')
        result = center_profile.confirm_price(self.db, token, csrf, offer_id)
        live = center_portal.dashboard(self.db, token)['live_prices']
        self.assertEqual(live[0]['confirmed'], result['date'])
        self.assertGreater(live[0]['updated'], 0)
        self.assertEqual(live[0]['amount_cents'], 45000)
        with self.assertRaisesRegex(booking.BookingError, 'already confirmed'):
            center_profile.confirm_price(self.db, token, csrf, offer_id)

    def test_one_exam_keeps_separate_prices_per_contrast(self):
        import center_price_apply
        token, csrf = self.login()
        rows = [{'exam': 'MRI', 'amount': a, 'price_type': 'fixed', 'contrast': c, 'interpretation_included': 'yes', 'note': ''}
                for a, c in (('400', 'without'), ('550', 'with'))]
        saved = center_portal.submit_prices(self.db, token, csrf, 'clinic-1', rows, True)['saved']
        for item in saved:
            center_price_apply.approve(self.db, item['id'], 'vikram')
        live = center_portal.dashboard(self.db, token)['live_prices']
        self.assertEqual(sorted((x['contrast'], x['amount_cents']) for x in live), [('with', 55000), ('without', 40000)])

    def test_ultrasound_and_xray_prices_reach_listing_text(self):
        import center_price_apply
        self.create()
        token, csrf = self.login()
        payload = CenterProfileTests.payload.__get__(self)
        rows = [{'exam': e, 'amount': a, 'price_type': 'fixed', 'contrast': 'unknown', 'interpretation_included': 'yes', 'note': ''}
                for e, a in (('Ultrasound', '300'), ('X-ray', '100'))]
        for item in center_portal.submit_prices(self.db, token, csrf, 'clinic-1', rows, True)['saved']:
            center_price_apply.approve(self.db, item['id'], 'vikram')
        self.assertEqual(sorted((x['exam'], x['amount_cents']) for x in center_portal.dashboard(self.db, token)['live_prices']),
                         [('Ultrasound', 30000), ('X-ray', 10000)])
        other = payload()['other']
        self.assertIn('Ultrasound: $300 (confirmed by center', other)
        self.assertIn('X-ray: $100 (confirmed by center', other)
        again = center_portal.submit_prices(self.db, token, csrf, 'clinic-1',
            [{'exam': 'Ultrasound', 'amount': '350', 'price_type': 'fixed', 'contrast': 'unknown', 'interpretation_included': 'yes', 'note': ''}], True)['saved'][0]
        center_price_apply.approve(self.db, again['id'], 'vikram')
        other = payload()['other']
        self.assertIn('Ultrasound: $350', other)
        self.assertNotIn('Ultrasound: $300', other)
        self.assertIn('X-ray: $100', other)



class CenterProfileTests(unittest.TestCase):
    """Address and website edits wait for admin approval."""
    setUp = test_booking.BookingTests.setUp
    tearDown = test_booking.BookingTests.tearDown
    verify_email = test_booking.BookingTests.verify_email
    verify_phone = test_booking.BookingTests.verify_phone
    create = test_booking.BookingTests.create
    login = CenterPortalTests.login

    def payload(self):
        with booking.connection(self.db) as db:
            return json.loads(db.execute("SELECT payload FROM entries WHERE entry_key='clinic-1' AND batch_id=(SELECT value FROM settings WHERE key='active_batch')").fetchone()['payload'])

    def test_edit_waits_then_approval_publishes(self):
        import center_profile
        self.create()
        token, csrf = self.login()
        old = self.payload().get('source')
        center_profile.submit(self.db, token, csrf, 'clinic-1', 'website', 'https://example.test/prices')
        self.assertEqual(self.payload().get('source'), old)
        board = center_portal.dashboard(self.db, token)
        self.assertEqual(board['profile_edits'][0]['status'], 'pending')
        item = center_profile.pending(self.db)[0]
        center_profile.approve(self.db, item['id'], 'admin1')
        self.assertEqual(self.payload()['source'], 'https://example.test/prices')
        self.assertEqual(center_portal.dashboard(self.db, token)['profile_edits'][0]['status'], 'approved')
        with self.assertRaises(center_profile.ProfileError):
            center_profile.approve(self.db, item['id'], 'admin1')

    def test_address_approval_flags_geocode(self):
        import center_profile
        self.create()
        token, csrf = self.login()
        center_profile.submit(self.db, token, csrf, 'clinic-1', 'address', '12 Main Street, Dayton, OH 45402')
        center_profile.approve(self.db, center_profile.pending(self.db)[0]['id'], 'admin1')
        data = self.payload()
        self.assertEqual(data['address'], '12 Main Street, Dayton, OH 45402')
        self.assertTrue(data['address_needs_geocode'])

    def test_reject_changes_nothing(self):
        import center_profile
        self.create()
        token, csrf = self.login()
        before = self.payload()
        center_profile.submit(self.db, token, csrf, 'clinic-1', 'address', '12 Main Street, Dayton, OH 45402')
        center_profile.reject(self.db, center_profile.pending(self.db)[0]['id'], 'admin1')
        self.assertEqual(self.payload(), before)
        self.assertEqual(center_profile.pending(self.db), [])

    def test_new_edit_replaces_pending_one(self):
        import center_profile
        self.create()
        token, csrf = self.login()
        center_profile.submit(self.db, token, csrf, 'clinic-1', 'address', '12 Main Street, Dayton, OH 45402')
        center_profile.submit(self.db, token, csrf, 'clinic-1', 'address', '14 Main Street, Dayton, OH 45402')
        rows = center_profile.pending(self.db)
        self.assertEqual([r['value'] for r in rows], ['14 Main Street, Dayton, OH 45402'])

    def test_validation_and_ownership(self):
        import center_profile
        self.create()
        token, csrf = self.login()
        for bad in ('http://example.test', 'https://nodot', 'https://u:p@example.test', 'https://exa mple.test', 'ftp://example.test'):
            with self.assertRaises(booking.BookingError):
                center_profile.submit(self.db, token, csrf, 'clinic-1', 'website', bad)
        with self.assertRaises(booking.BookingError):
            center_profile.submit(self.db, token, csrf, 'clinic-1', 'address', 'abc')
        with self.assertRaises(booking.BookingError):
            center_profile.submit(self.db, token, csrf, 'clinic-1', 'name', 'Other Name')
        with self.assertRaises(booking.BookingError):
            center_profile.submit(self.db, token, csrf, 'not-mine', 'address', '12 Main Street, Dayton, OH 45402')
        with self.assertRaises(booking.BookingError):
            center_profile.submit(self.db, token, 'wrong-csrf', 'clinic-1', 'address', '12 Main Street, Dayton, OH 45402')

    def test_rate_limit(self):
        import center_profile
        self.create()
        token, csrf = self.login()
        with self.assertRaisesRegex(booking.BookingError, 'Too many'):
            for i in range(25):
                center_profile.submit(self.db, token, csrf, 'clinic-1', 'address', f'{i + 10} Main Street, Dayton, OH 45402')


class AdminAlertTests(unittest.TestCase):
    """Administrators learn about changes that wait for approval."""
    setUp = test_booking.BookingTests.setUp
    tearDown = test_booking.BookingTests.tearDown
    verify_email = test_booking.BookingTests.verify_email
    verify_phone = test_booking.BookingTests.verify_phone
    create = test_booking.BookingTests.create
    login = CenterPortalTests.login

    def test_prices_and_profile_changes_email_admins_and_count(self):
        import admin_alerts, center_profile
        token, csrf = self.login()
        self.assertEqual(admin_alerts.pending_counts(self.db), (0, 0))
        with patch('admin_alerts.admin_emails', return_value=['boss@example.test', 'two@example.test']), patch('booking_mail.send_message') as send:
            center_portal.submit_prices(self.db, token, csrf, 'clinic-1',
                [{'exam': 'MRI', 'amount': '450', 'price_type': 'fixed', 'contrast': 'without', 'interpretation_included': 'yes', 'note': ''},
                 {'exam': 'X-ray', 'amount': '100', 'price_type': 'fixed', 'contrast': 'unknown', 'interpretation_included': 'yes', 'note': ''}], True)
            self.assertEqual([c.args[1] for c in send.call_args_list], ['boss@example.test', 'two@example.test'])
            body = send.call_args_list[0].args[0].get_body(preferencelist=('plain',)).get_content()
            self.assertIn('MRI $450 (without contrast)', body)
            self.assertIn('X-ray $100', body)
            self.assertIn('Center prices', body)
            center_profile.submit(self.db, token, csrf, 'clinic-1', 'website', 'https://example.test/p')
            self.assertEqual(send.call_count, 4)
        self.assertEqual(admin_alerts.pending_counts(self.db), (2, 1))

    def test_mail_failure_does_not_block_the_center(self):
        token, csrf = self.login()
        with patch('admin_alerts.admin_emails', return_value=['boss@example.test']), patch('booking_mail.send_message', side_effect=RuntimeError('smtp down')):
            result = center_portal.submit_prices(self.db, token, csrf, 'clinic-1',
                [{'exam': 'MRI', 'amount': '450', 'price_type': 'fixed', 'contrast': 'without', 'interpretation_included': 'yes', 'note': ''}], True)
        self.assertEqual(result['saved'][0]['status'], 'pending')


class AuditStoreTests(unittest.TestCase):
    """The audit trail is chained, append-only and shows centers only their own events."""

    def setUp(self):
        import audit, tempfile
        self.audit = audit
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, 'audit.sqlite3')
        audit.initialize(self.path)
        self.env = patch.dict(os.environ, {'AUDIT_DATABASE_URL': self.path, 'AUDIT_MODE': 'observe'})
        self.env.start()
        audit._health.update(at=0.0)

    def tearDown(self):
        self.env.stop()
        self.audit.unbind()
        self.dir.cleanup()

    def test_mail_results_and_directory_publishes_are_recorded(self):
        import booking_mail, core, tempfile
        from email.message import EmailMessage
        message = EmailMessage()
        message['Subject'] = 'Your code'
        message.set_content('secret body text')
        with patch('booking_mail._deliver') as deliver:
            booking_mail.send_message(message, 'Person@Example.test')
            deliver.side_effect = booking_mail.MailUnavailable('SMTP failed')
            with self.assertRaises(booking_mail.MailUnavailable):
                booking_mail.send_message(message, 'person@example.test')
        rows = self.audit.search(limit=10)
        self.assertEqual(sorted(r['result'] for r in rows if r['action'] == 'mail.send'), ['failed', 'ok'])
        blob = json.dumps(rows)
        self.assertNotIn('person@example.test'.lower(), blob.lower())
        self.assertNotIn('secret body text', blob)
        db = os.path.join(self.dir.name, 'dir.db')
        core.initialize(db)
        with core.connect(db) as c:
            c.execute("INSERT INTO batches VALUES('b1','h1','s',0,'staged','{}')")
            c.execute("INSERT INTO batches VALUES('b2','h2','s',0,'staged','{}')")
        core.publish('b1', db)
        core.publish('b2', db)
        core.publish('b1', db)
        kinds = [r['action'] for r in self.audit.search(limit=20) if r['action'].startswith('admin.directory')]
        self.assertEqual(sorted(kinds), ['admin.directory.publish', 'admin.directory.publish', 'admin.directory.rollback'])

    def test_a_failing_store_never_breaks_the_caller_in_observe_mode(self):
        bad = os.path.join(self.dir.name, 'missing-folder', 'audit.sqlite3')
        with patch.dict(os.environ, {'AUDIT_DATABASE_URL': bad, 'AUDIT_MODE': 'observe'}):
            self.assertIsNone(self.audit.log('center.price', scope='c1', actor='a@example.test', actor_type='center'))
        with patch.dict(os.environ, {'AUDIT_DATABASE_URL': bad, 'AUDIT_MODE': 'enforce'}):
            with self.assertRaises(self.audit.AuditUnavailable):
                self.audit.log('center.price', scope='c1', actor='a@example.test', actor_type='center')

    def test_rows_are_chained_and_edits_are_refused(self):
        a = self.audit
        for i in range(3):
            a.log('center.price', scope='c1', actor='a@example.test', actor_type='center', new=f'MRI ${i}')
        self.assertEqual(a.verify_chain(), (3, None, ''))
        con = sqlite3.connect(self.path)
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute("UPDATE audit_log SET new_value='x' WHERE seq=2")
        with self.assertRaises(sqlite3.DatabaseError):
            con.execute('DELETE FROM audit_log WHERE seq=2')
        con.close()

    def test_tampering_is_found(self):
        a = self.audit
        for i in range(3):
            a.log('center.price', scope='c1', new=str(i))
        con = sqlite3.connect(self.path)
        con.execute('DROP TRIGGER audit_no_update')
        con.execute("UPDATE audit_log SET new_value='changed' WHERE seq=2")
        con.commit()
        con.close()
        self.assertEqual(a.verify_chain()[1], 2)

    def test_center_sees_only_its_own_events_without_ip_or_staff(self):
        a = self.audit
        a.log('center.price', scope='c1', actor='a@example.test', actor_type='center', new='MRI $400')
        a.log('center.price', scope='c2', actor='b@example.test', actor_type='center', new='MRI $999')
        a.log('admin.price.approve', scope='c1', actor='vikram', actor_type='admin', new='MRI $400')
        a.log('center.verify', actor='a@example.test', actor_type='center')
        a.log('center.verify', actor='other@example.test', actor_type='center')
        feed = a.for_center(['c1'], ['a@example.test'])
        self.assertEqual(sorted(x['what'] for x in feed), ['Price approved by MySelfPay', 'Prices sent to publish', 'Signed in'])
        self.assertTrue(all(not ({'ip', 'actor', 'user_agent'} & set(x)) for x in feed))
        self.assertNotIn('vikram', json.dumps(feed))
        self.assertNotIn('999', json.dumps(feed))

    def test_proxy_count_decides_which_address_is_trusted(self):
        a = self.audit
        request = type('R', (), {'META': {'REMOTE_ADDR': '10.0.0.5', 'HTTP_X_FORWARDED_FOR': '6.6.6.6, 203.0.113.9, 10.0.0.9'}})()
        self.assertEqual(a.client_ip(request), '10.0.0.5')
        with patch.dict(os.environ, {'AUDIT_TRUSTED_PROXIES': '2'}):
            self.assertEqual(a.client_ip(request), '203.0.113.9')
        with patch.dict(os.environ, {'AUDIT_TRUSTED_PROXIES': '1'}):
            self.assertEqual(a.client_ip(request), '10.0.0.9')

    def test_enforce_mode_blocks_when_the_store_is_missing(self):
        with patch.dict(os.environ, {'AUDIT_DATABASE_URL': '', 'AUDIT_MODE': 'enforce'}):
            with self.assertRaises(self.audit.AuditUnavailable):
                self.audit.log('center.price')
        with patch.dict(os.environ, {'AUDIT_DATABASE_URL': '', 'AUDIT_MODE': 'observe'}):
            self.assertIsNone(self.audit.log('center.price'))

    def test_anchor_is_signed(self):
        a = self.audit
        a.log('center.price', scope='c1')
        with patch.dict(os.environ, {'AUDIT_ANCHOR_KEY': 'k'}):
            first = a.anchor()
        self.assertEqual(first['last_seq'], 1)
        self.assertEqual(len(first['signature']), 64)



if __name__ == '__main__':
    unittest.main()
