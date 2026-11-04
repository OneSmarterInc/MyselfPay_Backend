from django.conf import settings
from django.core.management.base import BaseCommand
import time
import booking
import booking_chat


class Command(BaseCommand):
    help = 'Retry submitted booking handoffs; --watch runs continuously.'

    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true')

    def handle(self, **options):
        while True:
            with booking.connection(settings.DOMAIN_DATABASE) as con:
                rows = con.execute("SELECT c.request_id FROM booking_chat c JOIN booking_requests r ON r.id=c.request_id WHERE c.state<>'ready' AND c.next_attempt<=? AND ((r.status IN ('requested','voicemail') AND r.link_expires>?) OR (r.status='scheduled' AND r.closed>?)) ORDER BY c.created LIMIT 50", (time.time(), time.time(), time.time()-30*86400)).fetchall()
            for row in rows:
                booking_chat.dispatch(settings.DOMAIN_DATABASE, row['request_id'])
            with booking.connection(settings.DOMAIN_DATABASE) as con:
                events = con.execute("SELECT e.request_id FROM booking_chat_events e JOIN booking_chat c ON c.request_id=e.request_id WHERE e.state<>'ready' AND e.next_attempt<=? AND c.state='ready' ORDER BY e.created LIMIT 50", (time.time(),)).fetchall()
            for event in events:
                booking_chat.dispatch_confirmation(settings.DOMAIN_DATABASE, event['request_id'])
            if not options['watch']:
                break
            time.sleep(5)
