from pathlib import Path
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
import core

class Command(BaseCommand):
    help = 'Import bundled research data into a NEW development database only.'

    def handle(self, **options):
        db = settings.DOMAIN_DATABASE
        if not settings.DEBUG:
            raise CommandError('Demo seeding requires DJANGO_DEBUG=true. Never seed a live database.')
        with core.connect(db) as con:
            if core.active_batch(con):
                raise CommandError('An active directory already exists; refusing to replace it.')
        root = Path(core.__file__).parent
        batch = core.stage_import(root / 'seed/directory.json', root / 'seed/mappings.json', db)
        core.publish(batch['id'], db)
        self.stdout.write(self.style.SUCCESS(f"Imported {batch['entry_count']} research entries and {batch['offer_count']} offers."))
