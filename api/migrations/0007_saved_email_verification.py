from pathlib import Path
from django.conf import settings
from django.db import migrations


def create_schema(apps, schema_editor):
    import core
    with core.connect(settings.DOMAIN_DATABASE) as db:
        db.executescript((Path(core.__file__).parent / 'ops/booking-verified-email.sql').read_text())
        # Preserve successful verification from earlier bookings, without retaining OTPs.
        db.execute("""INSERT INTO booking_verified_emails(owner_hash,email_hash,email_encrypted,verified_at)
            SELECT owner_hash,email_hash,email_encrypted,MAX(last_sent)
            FROM booking_email_checks WHERE state IN ('verified','consumed')
            GROUP BY owner_hash,email_hash,email_encrypted
            ON CONFLICT(owner_hash,email_hash) DO NOTHING""")


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('api', '0006_booking_phone')]
    operations = [migrations.RunPython(create_schema, reverse_code=migrations.RunPython.noop)]
