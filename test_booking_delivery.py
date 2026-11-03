import os
import unittest
from io import StringIO
from unittest.mock import patch
import booking
import booking_delivery
import booking_mail
import test_booking
from email.message import EmailMessage


class LiveDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_booking.BookingTests()
        self.fixture.setUp()
        self.env = patch.dict(os.environ, {'BOOKING_EMAIL_ENABLED': 'true', 'BOOKING_EMAIL_ALLOWED_RECIPIENTS': 'appointments@example.test'})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.fixture.tearDown()

    def test_request_commits_then_sends_once_and_retries_are_idempotent(self):
        def transport(message, recipient):
            with booking.connection(self.fixture.db) as con:
                self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM booking_requests').fetchone()['n'], 1)
            self.assertEqual(recipient, 'appointments@example.test')
            self.assertIn('New self-pay request', str(message['Subject']))
            self.assertIn(self.fixture.data['phone'], message.as_string())
            self.assertEqual(str(message['Reply-To']), 'patient@example.test')
        with patch('booking_mail.send_message', side_effect=transport) as send:
            result = self.fixture.create()
            self.assertEqual(result['delivery'], 'sent')
            self.assertEqual(self.fixture.create()['id'], result['id'])
            self.assertFalse(booking_delivery.deliver(self.fixture.db, result['id']))
            send.assert_called_once()

    def test_email_uses_verified_contact_and_escapes_content(self):
        with patch('booking_mail.send_message') as send:
            booking.create(self.fixture.db, 'guest-owner', self.fixture.data, 'https://portal.example.test',
                           account_contact={'email': 'registered@example.test', 'phone': '+1 555 010 2222'})
        message = send.call_args.args[0]
        self.assertEqual(str(message['Reply-To']), 'patient@example.test')
        html = message.get_body(preferencelist=('html',)).get_content()
        self.assertIn('mailto:patient@example.test', html)
        self.assertIn('tel:+16145550142', html)
        self.assertNotIn('mailto:registered@example.test', html)
        self.assertNotIn('/#clinic/', html)
        self.assertEqual(html.count('<a href='), 2)
        self.assertIn('Preferred appointment times', html)
        self.assertIn('America/New_York', html)
        self.assertIn('Clinic will confirm', html)
        payload={'recipient':'appointments@example.test','clinic_name':'<script>test</script>',
                 'service':'MRI','exam':'<b>Knee</b>','contrast':'without','slots':self.fixture.data['slots'],
                 'timezone':'America/New_York','patient_email':'patient@example.test','phone':'5550101111'}
        html=booking.notification(payload,'MSP-TEST').get_body(preferencelist=('html',)).get_content()
        self.assertIn('&lt;script&gt;', html)
        self.assertNotIn('<script>', html)

    def test_safe_failure_is_retryable_without_losing_or_recreating_booking(self):
        with patch('booking_mail.send_message', side_effect=booking_mail.MailUnavailable('unavailable')):
            result = self.fixture.create()
        self.assertEqual(result['delivery'], 'failed')
        with patch('booking_mail.send_message') as send:
            self.assertTrue(booking_delivery.deliver(self.fixture.db, result['id']))
            self.assertFalse(booking_delivery.deliver(self.fixture.db, result['id']))
            send.assert_called_once()
        self.assertEqual(booking.patient(self.fixture.db, result['id'], result['tracking_token'])['delivery'], 'sent')

    def test_uncertain_smtp_acceptance_is_never_automatically_resent(self):
        with patch('booking_mail.send_message', side_effect=booking_mail.MailDeliveryUncertain('unknown')):
            result = self.fixture.create()
        self.assertEqual(result['delivery'], 'uncertain')
        with patch('booking_mail.send_message') as send:
            self.assertFalse(booking_delivery.deliver(self.fixture.db, result['id']))
            send.assert_not_called()

    def test_clinic_receives_one_use_otp_and_can_update_appointment(self):
        with patch('booking_mail.send_message'):
            result = self.fixture.create()
        token = self.fixture.tokens(result)
        with patch('booking_mail.send_clinic_code') as send:
            issued = booking.issue_code(self.fixture.db, result['id'], token)
            code = send.call_args.args[2]
            self.assertEqual(send.call_args.args[0], 'appointments@example.test')
        self.assertEqual(issued['delivery'], 'sent')
        self.assertNotIn('preview_code', issued)
        session = booking.verify_code(self.fixture.db, result['id'], token, code)['session']
        details = booking.clinic_details(self.fixture.db, result['id'], session, 'scheduled')
        self.assertEqual(details['patient_email'], 'patient@example.test')
        with self.assertRaises(booking.BookingError):
            booking.verify_code(self.fixture.db, result['id'], token, code)

    def test_recipient_allowlist_is_enforced_before_mail_or_booking_creation(self):
        with patch.dict(os.environ, {'BOOKING_EMAIL_ALLOWED_RECIPIENTS': 'different@example.test'}), patch('booking_mail.send_message') as send:
            with self.assertRaises(booking.BookingError):
                self.fixture.create()
            send.assert_not_called()

    def test_transport_uses_implicit_tls_when_configured(self):
        env = {'SMTP_HOST':'smtp.example.test','SMTP_USERNAME':'sender@example.test','SMTP_PASSWORD':'synthetic-password',
               'SMTP_FROM':'sender@example.test','SMTP_PORT':'465','SMTP_USE_SSL':'true','SMTP_USE_TLS':'false'}
        with patch.dict(os.environ,env), patch('booking_mail.smtplib.SMTP_SSL') as transport:
            client=transport.return_value.__enter__.return_value
            client.send_message.return_value={}
            message=EmailMessage();message['Subject']='Synthetic test';message.set_content('Test')
            booking_mail.send_message(message,'appointments@example.test')
            transport.assert_called_once()
            client.starttls.assert_not_called()
            self.assertEqual(client.send_message.call_args.kwargs['to_addrs'],['appointments@example.test'])

    def test_plaintext_or_invalid_sender_configuration_is_rejected(self):
        env={'SMTP_HOST':'smtp.example.test','SMTP_USERNAME':'sender@example.test','SMTP_PASSWORD':'synthetic-password',
             'SMTP_FROM':'sender@example.test','SMTP_USE_TLS':'false','SMTP_USE_SSL':'false'}
        with patch.dict(os.environ,env), patch('booking_mail.smtplib.SMTP') as transport:
            with self.assertRaises(booking_mail.MailUnavailable):
                booking_mail.send_message(EmailMessage(),'appointments@example.test')
            transport.assert_not_called()


class TestCenterCommandTests(unittest.TestCase):
    def test_addition_preserves_existing_directory_and_is_idempotent(self):
        os.environ.setdefault('DJANGO_DEBUG', 'true')
        os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
        import django
        django.setup()
        from django.test import override_settings
        from django.core.management import call_command
        fixture = test_booking.BookingTests()
        fixture.setUp()
        try:
            with override_settings(DOMAIN_DATABASE=str(fixture.db), FRONTEND_ORIGIN='https://portal.example.test'), patch.dict(os.environ, {'TEST_CENTER_EMAIL': 'appointments@example.test'}):
                call_command('add_test_center', stdout=StringIO())
                with booking.connection(fixture.db) as con:
                    self.assertEqual(con.execute('SELECT COUNT(*) AS n FROM entries WHERE batch_id=?', ('batch',)).fetchone()['n'], 1)
                call_command('add_test_center', publish=True, stdout=StringIO())
                import core
                catalog = core.catalog(fixture.db)
                self.assertEqual(len(catalog['entries']), 2)
                self.assertEqual(len(catalog['offers']), 2)
                test = next(e for e in catalog['entries'] if e.get('is_test_center'))
                self.assertEqual(test['email'], 'appointments@example.test')
                self.assertEqual({offer['mod'] for offer in catalog['offers']}, {'MRI', 'CT'})
                call_command('add_test_center', publish=True, stdout=StringIO())
                self.assertEqual(core.catalog(fixture.db)['version'], catalog['version'])
                call_command('add_email_test_center', publish=True, email='giteshpawar3516@gmail.com', stdout=StringIO())
                updated = core.catalog(fixture.db)
                email_test = next(e for e in updated['entries'] if e['name'] == 'Gitesh Imaging Center')
                self.assertEqual(len(updated['entries']), 3)
                self.assertEqual(email_test['source'], '')
                self.assertEqual(email_test['email'], 'giteshpawar3516@gmail.com')
                self.assertTrue(email_test['is_test_center'])
                self.assertEqual(booking.appointment_email(email_test), 'giteshpawar3516@gmail.com')
                self.assertEqual({o['mod'] for o in updated['offers'] if o['entry']['name'] == 'Gitesh Imaging Center'}, {'MRI', 'CT'})
                call_command('add_email_test_center', publish=True, email='giteshpawar3516@gmail.com', stdout=StringIO())
                self.assertEqual(core.catalog(fixture.db)['version'], updated['version'])
        finally:
            fixture.tearDown()
