from pathlib import Path
from django.conf import settings
from django.db import migrations

def create_schema(apps, schema_editor):
    import core
    import accounts
    core.initialize(settings.DOMAIN_DATABASE)
    accounts.initialize(settings.DOMAIN_DATABASE)
    with core.connect(settings.DOMAIN_DATABASE) as con:
        con.executescript((Path(core.__file__).parent / 'ops/booking-schema.sql').read_text())

class Migration(migrations.Migration):
    initial = True
    atomic = False
    dependencies = []
    operations = [migrations.RunPython(create_schema, reverse_code=migrations.RunPython.noop)]
