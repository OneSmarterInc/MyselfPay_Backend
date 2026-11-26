import csv,re
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from center_pro.models import PreviousScanRecord

class Command(BaseCommand):
 help='Import ZIP-level 2025/2026 forecasts; preserve historical counts and center mappings.'
 def add_arguments(self,p):
  p.add_argument('csv_file');p.add_argument('--dry-run',action='store_true')
 def handle(self,*args,**options):
  data={}
  try:
   with open(options['csv_file'],encoding='utf-8-sig',newline='') as file:
    reader=csv.DictReader(file)
    if reader.fieldnames!=['zip_code','scans_2024','scans_2025','scans_2026']:raise CommandError('Expected zip_code,scans_2024,scans_2025,scans_2026 headers.')
    for line,row in enumerate(reader,2):
     zip5=row['zip_code']
     if None in row or not re.fullmatch(r'[0-9]{5}',zip5 or '') or zip5 in data:raise CommandError(f'Row {line}: invalid or duplicate ZIP.')
     counts={}
     for year in (2024,2025,2026):
      value=row[f'scans_{year}']
      if value is None or (value and (not re.fullmatch(r'[0-9]+',value) or int(value)>9223372036854775807)):raise CommandError(f'Row {line}: invalid count.')
      counts[f'scans_{year}']=int(value) if value else None
     data[zip5]=counts
  except OSError as error:raise CommandError(str(error)) from None
  if not data:raise CommandError('CSV has no records.')
  self.stdout.write(f'{len(data):,} ZIP rows; 2025 and 2026 are forecasts. Existing 2022-2024 values are preserved.')
  if options['dry_run']:return
  with transaction.atomic():
   existing=list(PreviousScanRecord.objects.select_for_update().filter(zip_code__in=data))
   totals={r.source_id for r in existing if r.source_id.startswith('zip-total-')}
   new=[]
   for zip5,values in data.items():
    if 'zip-total-'+zip5 not in totals:new.append(PreviousScanRecord(source_id='zip-total-'+zip5,center_name='ZIP-level total',zip_code=zip5,**values,source_note='2025 and 2026 forecasts from CasesByZip_2025_2026_Forecast.'))
   for row in existing:
    values=data[row.zip_code]
    row.scans_2025=values['scans_2025'];row.scans_2026=values['scans_2026']
    if row.scans_2024 is None:row.scans_2024=values['scans_2024']
   PreviousScanRecord.objects.bulk_create(new,batch_size=500)
   PreviousScanRecord.objects.bulk_update(existing,['scans_2024','scans_2025','scans_2026'],batch_size=200)
  self.stdout.write(self.style.SUCCESS(f'Imported {len(data):,} ZIP forecasts; updated {len(existing):,} existing rows; created {len(new):,} ZIP totals.'))
