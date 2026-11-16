"""List, approve or reject center-submitted prices. Approval never edits the published directory.

Approved rows are exported for the existing Vikram-approved directory change workflow.
"""
import csv
import sys
import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
import core


class Command(BaseCommand):
    help = 'List pending center price submissions, or mark one approved or rejected.'

    def add_arguments(self, parser):
        parser.add_argument('--status', default='pending', choices=('pending', 'approved', 'rejected', 'replaced'))
        parser.add_argument('--approve', metavar='ID')
        parser.add_argument('--reject', metavar='ID')
        parser.add_argument('--by', default='', help='Reviewer name, required with --approve or --reject.')

    def handle(self, *args, **options):
        decision = 'approved' if options['approve'] else 'rejected' if options['reject'] else None
        with core.connect(settings.DOMAIN_DATABASE) as db:
            if decision:
                ident = options['approve'] or options['reject']
                if not options['by'].strip():
                    raise CommandError('Pass --by with the reviewer name.')
                changed = db.execute("UPDATE center_price_submissions SET status=?,decided=?,decided_by=? WHERE id=? AND status='pending'",
                                     (decision, time.time(), options['by'].strip(), ident)).rowcount
                if not changed:
                    raise CommandError('No pending submission with that id.')
                self.stdout.write(f'{ident}: {decision}')
                return
            rows = db.execute('SELECT id,entry_key,exam,amount_cents,price_type,contrast,interpretation_included,note,submitted,status '
                              'FROM center_price_submissions WHERE status=? ORDER BY submitted', (options['status'],)).fetchall()
            out = csv.writer(sys.stdout if self.stdout._out is sys.stdout else self.stdout)
            out.writerow(['id', 'entry_key', 'exam', 'amount_usd', 'price_type', 'contrast', 'interpretation_included', 'note', 'submitted_unix', 'status'])
            for r in rows:
                out.writerow([r['id'], r['entry_key'], r['exam'], f"{r['amount_cents'] / 100:.2f}", r['price_type'], r['contrast'],
                              r['interpretation_included'], r['note'], int(r['submitted']), r['status']])
