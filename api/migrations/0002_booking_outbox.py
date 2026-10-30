from pathlib import Path
from django.conf import settings
from django.db import migrations


def create_outbox(apps, schema_editor):
    import core
    with core.connect(settings.DOMAIN_DATABASE) as con:
        con.executescript((Path(core.__file__).parent / 'ops/booking-outbox.sql').read_text())


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('api', '0001_initial')]
    operations = [migrations.RunPython(create_outbox, reverse_code=migrations.RunPython.noop)]
