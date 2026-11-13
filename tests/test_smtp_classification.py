"""No network calls: SMTP stages are simulated."""
from email.message import EmailMessage
import socket
import smtplib
import unittest
from unittest.mock import MagicMock, patch
import booking_mail


class SMTPClassificationTests(unittest.TestCase):
    def setUp(self):
        self.config = {'host':'smtp.example.test','username':'synthetic','password':'synthetic-secret',
                       'sender':'sender@example.test','name':'MySelfPay','port':465,'ssl':True,'tls':False}
        self.smtp=MagicMock(); self.smtp.send_message.return_value={}
        self.transport=MagicMock(); self.transport.__enter__.return_value=self.smtp

    def send(self):
        message=EmailMessage(); message.set_content('Synthetic')
        booking_mail.send_message(message,'recipient@example.test')

    def test_known_failure_stages_have_safe_actionable_messages(self):
        cases=[('connect',socket.gaierror('synthetic-secret'),'hostname'),
               ('connect',TimeoutError('synthetic-secret'),'connection timed out'),
               ('login',smtplib.SMTPAuthenticationError(535,b'synthetic-secret'),'authentication failed (535)'),
               ('send',smtplib.SMTPDataError(451,b'synthetic-secret'),'message rejected (451)'),
               ('send',smtplib.SMTPRecipientsRefused({'recipient@example.test':(550,b'synthetic-secret')}),'recipient rejected (550)')]
        for stage,error,expected in cases:
            with self.subTest(stage=stage,error=type(error).__name__):
                self.smtp.login.side_effect=error if stage=='login' else None
                self.smtp.send_message.side_effect=error if stage=='send' else None
                with patch('booking_mail.configuration',return_value=self.config), patch('booking_mail.smtplib.SMTP_SSL',side_effect=error if stage=='connect' else None,return_value=self.transport):
                    with self.assertRaises(booking_mail.MailUnavailable) as caught: self.send()
                    self.assertNotIsInstance(caught.exception,booking_mail.MailDeliveryUncertain)
                    self.assertIn(expected,str(caught.exception))
                    self.assertNotIn('synthetic-secret',str(caught.exception))

    def test_timeout_during_submission_is_uncertain(self):
        self.smtp.send_message.side_effect=TimeoutError()
        with patch('booking_mail.configuration',return_value=self.config),patch('booking_mail.smtplib.SMTP_SSL',return_value=self.transport):
            with self.assertRaises(booking_mail.MailDeliveryUncertain):self.send()

    def test_disconnect_after_confirmed_acceptance_does_not_mark_unsent(self):
        self.transport.__exit__.side_effect=smtplib.SMTPServerDisconnected()
        with patch('booking_mail.configuration',return_value=self.config),patch('booking_mail.smtplib.SMTP_SSL',return_value=self.transport):self.send()
