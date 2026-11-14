from django.conf import settings
from django.db import migrations


def create_schema(apps, schema_editor):
    import mobile_accounts
    mobile_accounts.initialize(settings.DOMAIN_DATABASE)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('api', '0012_smtp_configuration')]
    operations = [migrations.RunPython(create_schema, reverse_code=migrations.RunPython.noop)]
