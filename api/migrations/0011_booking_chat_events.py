from django.conf import settings
from django.db import migrations
from pathlib import Path


def install(apps, schema_editor):
    import core
    with core.connect(settings.DOMAIN_DATABASE) as con:
        con.executescript((Path(core.__file__).parent / 'ops/booking-chat-events.sql').read_text())


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('api', '0010_booking_chat')]
    operations = [migrations.RunPython(install, reverse_code=migrations.RunPython.noop)]
