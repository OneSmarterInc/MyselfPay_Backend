from django.core.management.base import BaseCommand
from admin_mailer import sync_contacts


class Command(BaseCommand):
    help = 'Refresh mailer rows from the active directory. Does not send email.'

    def handle(self, **options):
        count = sync_contacts()
        self.stdout.write(self.style.SUCCESS(f'Refreshed {count} center email rows. No emails sent.'))
