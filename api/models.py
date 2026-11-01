from django.db import models


class SpatialLocation(models.Model):
    id = models.CharField(max_length=64, primary_key=True)
    batch_id = models.TextField()
    entry_key = models.TextField()
    ordinal = models.IntegerField()
    h3_res7 = models.CharField(max_length=15)
    h3_res11 = models.CharField(max_length=15)
    location = models.TextField()

    class Meta:
        managed = False
        db_table = 'spatial_locations'
        indexes = [models.Index(fields=['batch_id', 'h3_res7'], name='spatial_batch_res7')]
