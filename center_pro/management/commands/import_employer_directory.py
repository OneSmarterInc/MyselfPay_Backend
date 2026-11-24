import csv
from decimal import Decimal,InvalidOperation
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from center_pro.models import Center,EmployerDirectory,NearbyCompany

class Command(BaseCommand):
    help='Import prepared operator/company CSV; reimports replace only the included operator groups.'
    def add_arguments(self,p):
        p.add_argument('csv_file');p.add_argument('--dry-run',action='store_true');p.add_argument('--mapping-file')
    def handle(self,*args,**o):
        from .prepare_employer_directory import FIELDS
        groups={};total=0;mapping={}
        if o.get('mapping_file'):
            with open(o['mapping_file'],encoding='utf-8-sig',newline='') as f:
                for row in csv.DictReader(f):
                    if row.get('source_id') and row.get('center_key','').strip():mapping[row['source_id']]=row['center_key'].strip()
        with open(o['csv_file'],encoding='utf-8-sig',newline='') as f:
            reader=csv.DictReader(f)
            if not reader.fieldnames or not set(FIELDS).issubset(reader.fieldnames):raise CommandError('CSV headers do not match the prepared directory format.')
            for row in reader:
                if row['source_id'] in mapping:row['center_key']=mapping[row['source_id']]
                if not row['source_id'] or not row['operator_name'] or not row['company_name']:raise CommandError('Missing operator or company name.')
                try:
                    number=int(row['source_row'])
                    text=row['distance_miles'].strip()
                    # Preserve descriptive/uncertain values, never invent distance.
                    distance=Decimal(text) if text else None
                    if distance is not None and (not distance.is_finite() or distance<0 or distance>99999999):raise CommandError('Invalid distance on source row '+str(number))
                except InvalidOperation:distance=None
                except ValueError:raise CommandError('Invalid source row.')
                row['_row']=number;row['_distance']=distance
                group=groups.setdefault(row['source_id'],[])
                if group and any(row[k]!=group[0][k] for k in ['operator_name','operator_address','operator_location','center_key']):raise CommandError('Inconsistent operator metadata for '+row['source_id'])
                group.append(row);total+=1
        keys={r[0]['center_key'] for r in groups.values() if r[0]['center_key']}
        centers={c.directory_key:c for c in Center.objects.filter(directory_key__in=keys,directory_active=True)}
        missing=keys-set(centers)
        if missing:raise CommandError(str(len(missing))+' center keys are absent. Run sync_center_pro and review mapping before import.')
        mapped=sum(bool(r[0]['center_key']) for r in groups.values())
        self.stdout.write(f'{total} company rows; {len(groups)} operators; {mapped} mapped; {len(groups)-mapped} awaiting mapping.')
        if o['dry_run']:return
        with transaction.atomic():
            for source_id,rows in groups.items():
                first=rows[0]
                group,_=EmployerDirectory.objects.update_or_create(source_id=source_id,defaults={'operator_name':first['operator_name'],'operator_address':first['operator_address'],'operator_location':first['operator_location'],'center':centers.get(first['center_key'])})
                group.companies.all().delete()
                seen=set();records=[]
                for r in rows:
                    if r['_row'] in seen:raise CommandError('Duplicate source row in '+source_id)
                    seen.add(r['_row'])
                    records.append(NearbyCompany(directory=group,source_row=r['_row'],company_name=r['company_name'],company_address=r['company_address'],public_email=r['public_email'],email_type=r['email_type'],business_phone=r['business_phone'],employee_count=r['employee_count'],distance_miles=r['_distance'],distance_text=r['distance_miles'],location_distance_status=r['location_distance_status'],verification_status=r['verification_status'],source_links=r['source_links']))
                NearbyCompany.objects.bulk_create(records,batch_size=500)
        self.stdout.write(self.style.SUCCESS('Import complete. Unmapped operators are retained but not shown to unrelated centers.'))
