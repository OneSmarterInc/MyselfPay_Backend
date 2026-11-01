from pathlib import Path
from django.conf import settings
from django.db import migrations


def create_account_zip(apps, schema_editor):
    import core
    with core.connect(settings.DOMAIN_DATABASE) as db:
        db.executescript((Path(core.__file__).parent / 'ops/sql/20261101-account-zip.sql').read_text())


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('api', '0002_booking_outbox')]
    operations = [migrations.RunPython(create_account_zip, reverse_code=migrations.RunPython.noop)]
