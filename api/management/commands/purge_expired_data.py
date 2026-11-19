"""Scheduled retention job: close expired requests, delete accounts 30 days after their last request
closes, and delete request details 30 days after closing. Run daily with a restricted database role."""
from django.core.management.base import BaseCommand
import account_forget
import booking
import core


class Command(BaseCommand):
    help = 'Delete accounts and request details whose 30-day retention has ended.'

    def handle(self, *args, **options):
        path = core.DB
        accounts = account_forget.purge_inactive(path)
        requests = booking.cleanup(path)
        self.stdout.write(f'Deleted {accounts} accounts and {requests} request records.')
