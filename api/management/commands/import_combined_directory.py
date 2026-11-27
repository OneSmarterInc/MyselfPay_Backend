import json
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from combined_directory import upsert

class Command(BaseCommand):
    help = "Upsert the combined workbook CSV into the MAIN directory; retain existing center IDs and unmatched centers."
    def add_arguments(self, parser):
        parser.add_argument('csv_file')
        parser.add_argument('--dry-run', action='store_true')
    def handle(self, *args, **options):
        try:result=upsert(options['csv_file'],settings.DOMAIN_DATABASE,options['dry_run'])
        except (ValueError,OSError) as exc:raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result,indent=2))
