from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
import booking
from mail_diagnostics import request_status


class Command(BaseCommand):
    help = 'Read-only delivery evidence for one request. Sends no email and displays no credentials or request tokens.'

    def add_arguments(self, parser):
        parser.add_argument('request_id')

    def handle(self, **options):
        try:
            info = request_status(settings.DOMAIN_DATABASE, options['request_id'])
        except booking.BookingError as exc:
            raise CommandError(str(exc)) from None
        for key in ('id', 'center', 'recipient', 'current_center_recipient', 'route', 'state', 'attempts', 'smtp_source'):
            self.stdout.write(f'{key}: {info[key]}')
        for note in info['notes']:
            self.stdout.write('- ' + note)
