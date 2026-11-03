from pathlib import Path
from django.conf import settings
from django.db import migrations


def install(apps, schema_editor):
    import core
    import booking
    with core.connect(settings.DOMAIN_DATABASE) as db:
        db.executescript((Path(core.__file__).parent / 'ops/center-portal.sql').read_text())
        rows = db.execute('SELECT id,payload FROM booking_requests').fetchall()
        for row in rows:
            payload = booking.unpack(row)
            db.execute('INSERT INTO center_request_routes(request_id,clinic_id,email_hash) VALUES(?,?,?) ON CONFLICT(request_id) DO NOTHING',
                       (row['id'], payload['clinic_id'], booking.digest(payload['recipient'].strip().lower())))


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('api', '0008_five_digit_zip')]
    operations = [migrations.RunPython(install, reverse_code=migrations.RunPython.noop)]
