"""Booking regression tests using isolated SQLite and synthetic contacts only."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import gc
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet
import booking
import core


class BookingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / 'booking.sqlite3'
        self.env = patch.dict(os.environ, {'BOOKING_ENCRYPTION_KEY': Fernet.generate_key().decode()})
        self.env.start()
        core.initialize(self.db)
        booking.initialize_local(self.db)
        self.center = {'name': 'Synthetic Imaging', 'area': 'Test city',
                       'email': 'appointments@example.test', 'appointment_email_approved': True}
        with booking.connection(self.db) as con:
            con.execute("INSERT INTO batches VALUES('batch','hash','synthetic',0,'published','{}')")
            con.execute("INSERT INTO settings VALUES('active_batch','batch')")
            con.execute('INSERT INTO entries VALUES(?,?,?,?)', ('batch', 'clinic-1', 'h', json.dumps(self.center)))
        self.data = {'clinic_id': 'clinic-1', 'idempotency_key': 'synthetic-request-key',
                     'first_name': 'Synthetic Jamie', 'phone': '+16145550142', 'service': 'MRI',
                     'exam': 'Knee', 'contrast': 'without', 'timezone': 'America/New_York',
                     'consent': True, 'slots': [
                         {'date': (datetime.now() + timedelta(days=n)).date().isoformat(), 'period': 'morning'}
                         for n in (2, 3, 4)]}
        self.data.update(self.verify_email('guest-owner', 'patient@example.test'))
        self.data.update(self.verify_phone('guest-owner', self.data['phone']))

    def tearDown(self):
        self.env.stop()
        gc.collect()
        self.temp.cleanup()

    def create(self, data=None):
        return booking.create(self.db, 'guest-owner', data or self.data, 'http://127.0.0.1:8765')

    def verify_email(self, owner, email):
        with patch('booking_mail.send_patient_code') as send:
            issued = booking.request_patient_code(self.db, owner, email)
            code = send.call_args.args[1]
        return booking.verify_patient_code(self.db, owner, issued['verification_id'], code)

    def verify_phone(self, owner, phone):
        import booking_phone
        with patch('booking_sms.configuration', return_value={}), patch('booking_sms.send_code', return_value='VE'+'1'*32):
            issued=booking_phone.request_code(self.db,owner,phone)
        with patch('booking_sms.check_code',return_value=True):
            proof=booking_phone.verify_code(self.db,owner,issued['verification_id'],'123456')
        return {'phone_verification_id':proof['verification_id'],'phone_verification_token':proof['verification_token']}

    def tokens(self, request):
        preview = booking.patient(self.db, request['id'], request['tracking_token'], preview=True)
        return preview['clinic_url'].rsplit('/', 1)[1]

    def authorize_clinic(self, request):
        token = self.tokens(request)
        code = booking.issue_code(self.db, request['id'], token, preview=True)['preview_code']
        return booking.verify_code(self.db, request['id'], token, code)['session']

    def test_complete_flow_and_patient_status(self):
        request = self.create()
        self.assertEqual(request['delivery'], 'suppressed')
        session = self.authorize_clinic(request)
        details = booking.clinic_details(self.db, request['id'], session)
        self.assertEqual(details['phone'], self.data['phone'])
        self.assertEqual(len(details['slots']), 3)
        booking.clinic_details(self.db, request['id'], session, 'voicemail')
        booking.clinic_details(self.db, request['id'], session, 'scheduled')
        result = booking.patient(self.db, request['id'], request['tracking_token'])
        self.assertEqual(result['status'], 'scheduled')
        self.assertNotIn('phone', result)
        self.assertNotIn('first_name', result)
        with self.assertRaises(booking.BookingError):
            booking.clinic_details(self.db, request['id'], session, 'unable')

    def test_mail_never_connects_and_notification_has_direct_contact_actions(self):
        with patch('smtplib.SMTP') as smtp, patch('smtplib.SMTP_SSL') as ssl:
            result = self.create()
            token = self.tokens(result)
            booking.issue_code(self.db, result['id'], token, preview=True)
            smtp.assert_not_called()
            ssl.assert_not_called()
        preview = booking.patient(self.db, result['id'], result['tracking_token'], preview=True)
        self.assertIn('appointments@example.test', preview['notification'])
        self.assertIn(self.data['phone'], preview['notification'])
        self.assertNotIn('Open request', preview['notification'])
        self.assertNotIn(self.data['first_name'], preview['notification'])

    def test_recipient_cannot_be_overridden(self):
        data = {**self.data, 'email': 'attacker@example.test', 'recipient': 'attacker@example.test'}
        r = self.create(data)
        preview = booking.patient(self.db, r['id'], r['tracking_token'], preview=True)
        self.assertNotIn('attacker', preview['notification'])

    def test_encrypted_payload_and_hashed_capabilities(self):
        r = self.create()
        with booking.connection(self.db) as con:
            row = con.execute('SELECT * FROM booking_requests').fetchone()
            raw = json.dumps(dict(row))
        for private in (self.data['phone'], self.data['first_name'], r['tracking_token']):
            self.assertNotIn(private, raw)

    def test_retries_and_concurrent_duplicates(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.create(), range(4)))
        self.assertEqual(len({r['id'] for r in results}), 1)
        with booking.connection(self.db) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM booking_requests').fetchone()['n'], 1)
        with self.assertRaises(booking.BookingError):
            self.create({**self.data, 'first_name': 'Other'})

    def test_validation(self):
        bad = [{'consent': False}, {'first_name': '\r\nInjected'}, {'phone': 'bad'},
               {'slots': []}, {'slots': self.data['slots'] * 2},
               {'slots': [self.data['slots'][0]] * 2}, {'timezone': 'made-up'},
               {'slots': [{'date': '2000-01-01', 'period': 'morning'}]},
               {'slots': [{'date': '2099-02-30', 'period': 'morning'}]},
               {'slots': [{'date': '2099-01-01', 'period': 'morning'}]},
               {'slots': [{'date': self.data['slots'][0]['date'], 'period': 'midnight'}]},
               {'service': 'injected'}, {'clinic_id': 'unknown'}, {'offer_id': 'wrong'}]
        for fields in bad:
            with self.subTest(fields=fields), self.assertRaises(booking.BookingError):
                self.create({**self.data, **fields})

    def test_otp_one_use_and_session_expiry(self):
        r = self.create()
        token = self.tokens(r)
        code = booking.issue_code(self.db, r['id'], token, preview=True)['preview_code']
        auth = booking.verify_code(self.db, r['id'], token, code)
        with self.assertRaises(booking.BookingError):
            booking.verify_code(self.db, r['id'], token, code)
        with patch('booking.time.time', return_value=auth['expires_at'] + 1):
            with self.assertRaises(booking.BookingError):
                booking.clinic_details(self.db, r['id'], auth['session'])

    def test_wrong_codes_are_counted_and_throttled(self):
        r = self.create()
        token = self.tokens(r)
        code = booking.issue_code(self.db, r['id'], token, preview=True)['preview_code']
        wrong = '000000' if code != '000000' else '000001'
        for _ in range(5):
            with self.assertRaises(booking.BookingError):
                booking.verify_code(self.db, r['id'], token, wrong)
        with self.assertRaises(booking.BookingError) as error:
            booking.verify_code(self.db, r['id'], token, code)
        self.assertEqual(error.exception.status, 429)

    def test_resend_and_link_expiry(self):
        r = self.create()
        token = self.tokens(r)
        issued = booking.issue_code(self.db, r['id'], token, preview=True)
        with self.assertRaises(booking.BookingError):
            booking.issue_code(self.db, r['id'], token, preview=True)
        with patch('booking.time.time', return_value=time.time() + 601):
            with self.assertRaises(booking.BookingError):
                booking.verify_code(self.db, r['id'], token, issued['preview_code'])
        with patch('booking.time.time', return_value=time.time() + 73 * 3600):
            with self.assertRaises(booking.BookingError):
                booking.issue_code(self.db, r['id'], token, preview=True)

    def test_capabilities_cannot_access_other_requests(self):
        a = self.create()
        proof = self.verify_email('guest-owner', 'patient2@example.test')
        b = self.create({**self.data, **proof, **self.verify_phone('guest-owner', '+16145550143'), 'phone': '+16145550143', 'idempotency_key': 'second'})
        with self.assertRaises(booking.BookingError):
            booking.patient(self.db, b['id'], a['tracking_token'])
        with self.assertRaises(booking.BookingError):
            booking.clinic_details(self.db, b['id'], self.authorize_clinic(a))

    def test_lock_revokes_session(self):
        r = self.create()
        session = self.authorize_clinic(r)
        booking.lock_session(self.db, r['id'], session)
        with self.assertRaises(booking.BookingError):
            booking.clinic_details(self.db, r['id'], session)

    def test_retention_cleanup(self):
        r = self.create()
        with patch('booking.time.time', return_value=time.time() + 34 * 86400):
            self.assertEqual(booking.cleanup(self.db), 1)
            with self.assertRaises(booking.BookingError):
                booking.patient(self.db, r['id'], r['tracking_token'])

    def test_unapproved_email_is_not_used(self):
        for scope in ('Billing', 'Referral only', 'Privacy', 'Not for scheduling'):
            self.assertEqual(booking.appointment_email({'email': 'x@example.test', 'email_scope': scope}), '')
        self.assertEqual(booking.appointment_email({'email': 'x@example.test?bcc=y@example.test', 'appointment_email_approved': True}), '')


    def test_patient_verification_required_owner_bound_and_single_use(self):
        missing = dict(self.data)
        missing.pop('verification_token')
        with self.assertRaises(booking.BookingError):
            self.create(missing)
        with self.assertRaises(booking.BookingError):
            booking.create(self.db, 'other-owner', self.data, 'http://127.0.0.1:8765')
        self.create()
        with self.assertRaises(booking.BookingError):
            self.create({**self.data, 'idempotency_key': 'new-key'})

    def test_patient_code_wrong_attempts_expiry_and_no_code_disclosure(self):
        with patch('booking_mail.send_patient_code') as send:
            issued = booking.request_patient_code(self.db, 'new-owner', 'check@example.test')
            code = send.call_args.args[1]
        self.assertNotIn('code', issued)
        self.assertNotIn('preview_code', issued)
        with patch('booking.time.time', return_value=time.time() + 601):
            with self.assertRaises(booking.BookingError):
                booking.verify_patient_code(self.db, 'new-owner', issued['verification_id'], code)
        wrong = '000000' if code != '000000' else '000001'
        for _ in range(8):
            with self.assertRaises(booking.BookingError) as error:
                booking.verify_patient_code(self.db, 'new-owner', issued['verification_id'], wrong)
            self.assertEqual(error.exception.status,400)
        verified=booking.verify_patient_code(self.db, 'new-owner', issued['verification_id'], code)
        self.assertIn('verification_token',verified)
        with patch('booking_mail.send_patient_code') as send:
            for _ in range(8):booking.request_patient_code(self.db,'resend-owner','resend@example.test')
            self.assertEqual(send.call_count,8)

    def test_smtp_failure_does_not_verify_or_accept_code(self):
        import booking_mail
        with patch('booking_mail.send_patient_code', side_effect=booking_mail.MailUnavailable('Unavailable')):
            with self.assertRaises(booking.BookingError) as error:
                booking.request_patient_code(self.db, 'failed-owner', 'failure@example.test')
        self.assertEqual(error.exception.status, 503)
        with booking.connection(self.db) as con:
            row = con.execute('SELECT * FROM booking_email_checks WHERE owner_hash=?', (booking.digest('failed-owner'),)).fetchone()
        self.assertEqual(row['state'], 'failed')
        self.assertIsNone(row['code_hash'])

    def test_patient_smtp_transport_uses_tls_and_only_intended_recipient(self):
        import booking_mail
        with patch.dict(os.environ, {'SMTP_HOST': 'smtp.protonmail.ch', 'SMTP_USERNAME': 'sender@example.test',
                                     'SMTP_PASSWORD': 'synthetic-test-password', 'SMTP_FROM': 'sender@example.test', 'SMTP_PORT': '587'}):
            with patch('booking_mail.smtplib.SMTP') as smtp:
                transport = smtp.return_value.__enter__.return_value
                transport.send_message.return_value = {}
                booking_mail.send_patient_code('patient@example.test', '123456')
                transport.starttls.assert_called_once()
                message = transport.send_message.call_args.args[0]
                self.assertEqual(str(message['To']), 'patient@example.test')
                self.assertNotIn('Cc', message)
                self.assertNotIn('Bcc', message)


if __name__ == '__main__':
    unittest.main()

