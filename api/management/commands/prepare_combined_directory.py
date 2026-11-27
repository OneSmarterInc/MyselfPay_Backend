import json
from django.core.management.base import BaseCommand, CommandError
from combined_directory import prepare

class Command(BaseCommand):
    help = "Convert the combined imaging workbook to a lossless main-directory CSV."
    def add_arguments(self, parser):
        parser.add_argument('xlsx_file')
        parser.add_argument('csv_file')
    def handle(self, *args, **options):
        try:result=prepare(options['xlsx_file'],options['csv_file'])
        except (ValueError,OSError) as exc:raise CommandError(str(exc)) from exc
        self.stdout.write(json.dumps(result,indent=2))
