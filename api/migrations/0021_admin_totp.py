import uuid
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('api', '0020_center_profile_edits'), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [migrations.CreateModel(name='AdminTOTP', fields=[
        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
        ('secret_encrypted', models.TextField(editable=False)),
        ('enabled', models.BooleanField(default=False)),
        ('last_counter', models.BigIntegerField(default=-1)),
        ('failed_attempts', models.PositiveIntegerField(default=0)),
        ('locked_until', models.DateTimeField(blank=True, null=True)),
        ('recovery_hashes', models.JSONField(default=list, editable=False)),
        ('generation', models.UUIDField(default=uuid.uuid4, editable=False)),
        ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
    ])]
