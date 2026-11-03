from pathlib import Path
from django.conf import settings
from django.db import migrations


def create_phone_schema(apps, schema_editor):
    import core
    with core.connect(settings.DOMAIN_DATABASE) as db:
        db.executescript((Path(core.__file__).parent / 'ops/booking-phone.sql').read_text())


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('api', '0005_admin_portal')]
    operations = [migrations.RunPython(create_phone_schema, reverse_code=migrations.RunPython.noop)]
