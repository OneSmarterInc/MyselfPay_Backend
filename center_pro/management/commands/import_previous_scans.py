import csv,re
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from center_pro.models import Center,PreviousScanRecord

class Command(BaseCommand):
 help='Import center ZIP-level Medicare scan history for 2022, 2023 and 2024.'
 def add_arguments(self,p):p.add_argument('csv_file');p.add_argument('--dry-run',action='store_true')
 def handle(self,*args,**o):
  from .prepare_previous_scans import FIELDS
  records=[];ids=set()
  with open(o['csv_file'],encoding='utf-8-sig',newline='') as f:
   reader=csv.DictReader(f)
   if not reader.fieldnames or not set(FIELDS).issubset(reader.fieldnames):raise CommandError('CSV headers do not match previous-scans format.')
   for row in reader:
    if not row['source_id'] or row['source_id'] in ids or not row['center_name']:raise CommandError('Missing name or duplicate source ID.')
    ids.add(row['source_id'])
    if row['zip_code'] and not re.fullmatch(r'\d{5}',row['zip_code']):raise CommandError('Invalid ZIP for '+row['source_id'])
    for year in (2022,2023,2024):
     key=f'scans_{year}';value=row[key].strip()
     if value and (not value.isdigit() or int(value)>9223372036854775807):raise CommandError('Invalid count for '+row['source_id'])
     row[key]=int(value) if value else None
    records.append(row)
  keys={r['center_key'] for r in records if r['center_key']}
  centers={c.directory_key:c for c in Center.objects.filter(directory_key__in=keys,directory_active=True)}
  if keys-set(centers):raise CommandError('Some center keys do not exist. Run sync_center_pro and review mapping.')
  self.stdout.write(f'{len(records)} rows; {sum(bool(r["center_key"]) for r in records)} mapped centers; missing counts remain null.')
  if o['dry_run']:return
  with transaction.atomic():
   for row in records:
    source=row.pop('source_id');key=row.pop('center_key')
    PreviousScanRecord.objects.update_or_create(source_id=source,defaults={**row,'center':centers.get(key)})
  self.stdout.write(self.style.SUCCESS('Previous ZIP scans imported without changing bookings or center prices.'))
