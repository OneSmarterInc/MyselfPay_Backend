from pathlib import Path
from django.conf import settings
from django.db import migrations


def install(apps, schema_editor):
    import core
    with core.connect(settings.DOMAIN_DATABASE) as db:
        db.executescript((Path(core.__file__).parent / 'ops/center-prices.sql').read_text())


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('api', '0012_smtp_configuration')]
    operations = [migrations.RunPython(install, reverse_code=migrations.RunPython.noop)]
