from django.db import migrations,models

class Migration(migrations.Migration):
 dependencies=[('center_pro','0009_previousscanrecord')]
 operations=[migrations.AddField(model_name='previousscanrecord',name='scans_2025',field=models.PositiveBigIntegerField(null=True,blank=True)),migrations.AddField(model_name='previousscanrecord',name='scans_2026',field=models.PositiveBigIntegerField(null=True,blank=True))]
