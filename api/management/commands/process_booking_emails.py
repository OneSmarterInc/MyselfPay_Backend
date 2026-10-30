from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
import time
import core
import booking_delivery


class Command(BaseCommand):
    help = 'Inspect pending clinic notifications; --send retries queued/safe-failed messages.'

    def add_arguments(self, parser):
        parser.add_argument('--send', action='store_true')
        parser.add_argument('--limit', type=int, default=100)

    def handle(self, **options):
        if not 1 <= options['limit'] <= 1000:
            raise CommandError('Limit must be between 1 and 1000.')
        with core.connect(settings.DOMAIN_DATABASE) as con:
            rows = con.execute("SELECT o.request_id FROM booking_outbox o JOIN booking_requests r ON r.id=o.request_id WHERE o.state IN ('queued','failed','sending') AND o.attempts<5 AND r.link_expires>? AND r.status NOT IN ('scheduled','unable','expired') ORDER BY o.created LIMIT ?", (time.time(), options['limit'])).fetchall()
        if not options['send']:
            self.stdout.write(f"Pending/reviewable notifications: {len(rows)}. No email sent. Use --send to process.")
            return
        if not booking_delivery.enabled():
            raise CommandError('BOOKING_EMAIL_ENABLED is not true.')
        sent = sum(booking_delivery.deliver(settings.DOMAIN_DATABASE, row['request_id']) for row in rows)
        self.stdout.write(f'SMTP accepted: {sent}. Inspected: {len(rows)}. Uncertain deliveries are never automatically replayed.')
