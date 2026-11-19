import time
from django.core.management.base import BaseCommand
from django.db import close_old_connections
from django.utils import timezone
from datetime import timedelta
from api.models import MailerMailbox
import admin_mailer


class Command(BaseCommand):
    help = 'Send queued admin campaigns and sync matching IMAP replies. No campaigns are created by this command.'

    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true')
        parser.add_argument('--sync-contacts', action='store_true')

    def handle(self, **options):
        if options['sync_contacts']:
            self.stdout.write(f'Directory emails: {admin_mailer.sync_contacts()}')
        try:
            while True:
                close_old_connections()
                sent = admin_mailer.send_pending()
                mailbox = MailerMailbox.objects.filter(pk=1, enabled=True).first()
                if mailbox and (mailbox.sync_requested or not mailbox.last_sync or mailbox.last_sync < timezone.now() - timedelta(seconds=60)):
                    admin_mailer.sync_replies()
                if sent:
                    self.stdout.write(f'SMTP accepted: {sent}')
                if not options['watch']:
                    break
                time.sleep(3)
        except KeyboardInterrupt:
            self.stdout.write('Mailer worker stopped.')
