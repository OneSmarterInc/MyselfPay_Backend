from django.conf import settings
from django.core.management.base import BaseCommand
import core
import hospital_rates


class Command(BaseCommand):
    help = 'Replace hospital_rates from the approved review CSV. Run on the hosted database only after approve db.'

    def add_arguments(self, parser):
        parser.add_argument('csv_path')
        parser.add_argument('--checked', required=True, help='Month the files were checked, for example 2026-11')

    def handle(self, csv_path, checked, **options):
        with core.connect(settings.DOMAIN_DATABASE) as db:
            db.executescript(open(core.__file__.rsplit('/', 1)[0] + '/ops/hospital-rates.sql').read())
            count = hospital_rates.load_csv(db, csv_path, checked)
        self.stdout.write(f'Loaded {count} hospital rate rows.')
