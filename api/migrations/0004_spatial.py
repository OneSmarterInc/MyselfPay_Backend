from django.conf import settings
from django.db import migrations, models


def create_spatial(apps, schema_editor):
    import core
    import spatial
    with core.connect(settings.DOMAIN_DATABASE) as db:
        spatial.initialize(db)


class Migration(migrations.Migration):
    atomic = False
    dependencies = [('api', '0003_account_zip')]
    operations = [
        migrations.CreateModel(
            name='SpatialLocation',
            fields=[('id', models.CharField(max_length=64, primary_key=True, serialize=False)), ('batch_id', models.TextField()), ('entry_key', models.TextField()), ('ordinal', models.IntegerField()), ('h3_res7', models.CharField(max_length=15)), ('h3_res11', models.CharField(max_length=15)), ('location', models.TextField())],
            options={'managed': False, 'db_table': 'spatial_locations', 'indexes': [models.Index(fields=['batch_id', 'h3_res7'], name='spatial_batch_res7')]},
        ),
        migrations.RunPython(create_spatial, reverse_code=migrations.RunPython.noop),
    ]
