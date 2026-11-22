from pathlib import Path
from django.conf import settings
from django.db import migrations


def install(apps, schema_editor):
    import core
    with core.connect(settings.DOMAIN_DATABASE) as db:
        db.executescript((Path(core.__file__).parent / 'ops/hospital-rates.sql').read_text())


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('api', '0021_admin_totp')]
    operations = [migrations.RunPython(install, reverse_code=migrations.RunPython.noop)]
