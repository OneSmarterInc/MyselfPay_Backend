from django.conf import settings
from django.db import migrations


def create_schema(apps, schema_editor):
    import password_reset
    password_reset.initialize(settings.DOMAIN_DATABASE)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('api','0013_mobile_accounts')]
    operations = [migrations.RunPython(create_schema,reverse_code=migrations.RunPython.noop)]
