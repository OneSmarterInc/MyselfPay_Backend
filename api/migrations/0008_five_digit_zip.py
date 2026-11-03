from django.conf import settings
from django.db import migrations


def normalize_zip(apps, schema_editor):
    import core
    with core.connect(settings.DOMAIN_DATABASE) as db:
        db.execute("UPDATE account_zip_codes SET zip_code=SUBSTR(zip_code,1,5) WHERE LENGTH(zip_code)=10 AND SUBSTR(zip_code,6,1)='-'")


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('api','0007_saved_email_verification')]
    operations = [migrations.RunPython(normalize_zip, reverse_code=migrations.RunPython.noop)]
