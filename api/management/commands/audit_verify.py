from django.core.management.base import BaseCommand, CommandError
import audit


class Command(BaseCommand):
    help = 'Recompute every hash in the audit log and report the first broken row.'

    def handle(self, *args, **options):
        if not audit.url():
            raise CommandError('AUDIT_DATABASE_URL is not set.')
        checked, bad, why = audit.verify_chain()
        if bad is not None:
            raise CommandError(f'CHAIN BROKEN at row {bad}: {why} ({checked} rows read)')
        self.stdout.write(f'Chain intact: {checked} rows checked.')
