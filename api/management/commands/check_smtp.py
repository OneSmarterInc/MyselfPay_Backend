import os
from email.message import EmailMessage
from django.core.management.base import BaseCommand, CommandError
import booking_mail
import booking


class Command(BaseCommand):
    help = 'Validate mail settings without exposing credentials; --send sends a test to TEST_CENTER_EMAIL.'

    def add_arguments(self, parser):
        parser.add_argument('--send', action='store_true')

    def handle(self, **options):
        values = booking_mail.configuration()
        missing = [key for key in ('host', 'username', 'password', 'sender') if not values.get(key)]
        if missing:
            raise CommandError('Missing SMTP settings: ' + ', '.join(missing))
        if not options['send']:
            self.stdout.write('Required SMTP settings are present (source: ' + values['source'] + '). No connection made and no email sent. Use --send to verify SMTP acceptance.')
            return
        try:
            recipient = booking.email_address(values['test_recipient'])
        except booking.BookingError:
            raise CommandError('Set TEST_CENTER_EMAIL to the confirmed test recipient.') from None
        message = EmailMessage()
        message['From'] = 'MySelfPay'
        message['Subject'] = 'MySelfPay booking email configuration test'
        message.set_content('MySelfPay SMTP configuration test.\n\nThis email confirms that the sending server accepted a message addressed to your test center. It is not an appointment request.\n\nMySelfPay')
        try:
            booking_mail.send_message(message, recipient)
        except booking_mail.MailUnavailable as exc:
            raise CommandError(str(exc)) from None
        self.stdout.write(self.style.SUCCESS('SMTP server accepted the test email. Check the recipient inbox/spam folder.'))
