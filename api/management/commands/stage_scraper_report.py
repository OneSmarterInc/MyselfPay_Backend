import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

import core
from scrapper.staging import stage


class Command(BaseCommand):
    help = "Stage scraper observations against the current directory for review."

    def add_arguments(self, parser):
        parser.add_argument("report", type=Path)
        parser.add_argument("--bindings", type=Path, help="Reviewed JSON mapping observation IDs to directory entry keys")

    def handle(self, *args, **options):
        try:
            report = json.loads(options["report"].read_text(encoding="utf-8-sig"))
            path = options["bindings"]
            bindings = json.loads(path.read_text(encoding="utf-8-sig")) if path else {}
            with core.connect(settings.DOMAIN_DATABASE) as db:
                result = stage(db, report, bindings)
            self.stdout.write(json.dumps(result))
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise CommandError(str(exc)) from exc
