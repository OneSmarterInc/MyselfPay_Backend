from django.db.models import Q,Case,When,Value,IntegerField
from django.db.models.functions import Abs,Cast,NullIf
from rest_framework.exceptions import PermissionDenied
from .views import CenterView,Pagination
from .models import PreviousScanRecord

def saved_center_zip(center):
 if not center.directory_key:return ''
 import json,re,core
 from .integration import DomainConnection
 db=DomainConnection()
 row=db.execute('SELECT payload FROM entries WHERE batch_id=? AND entry_key=?',(core.active_batch(db),center.directory_key)).fetchone()
 if not row:return ''
 entry=json.loads(row['payload'])
 values=[entry.get('zip'),entry.get('zip_code'),entry.get('postal_code')]
 values.extend(location.get('zip') or location.get('zip_code') or location.get('postal_code') for location in entry.get('locations',[]))
 for value in values:
  match=re.fullmatch(r'(\d{5})(?:-\d{4})?',str(value or '').strip())
  if match:return match[1]
 match=re.search(r'\b(\d{5})(?:-\d{4})?\b',' '.join(str(entry.get(k) or '') for k in ('address','coverage')))
 return match[1] if match else ''

class PreviousScansView(CenterView):
 def get(self,request,center_id):
  member=self.membership(request,center_id)
  if member.role!='owner':raise PermissionDenied('Owner access is required.')
  rows=PreviousScanRecord.objects.exclude(source_id__startswith="zip-total-")
  query=request.query_params.get('search','').strip()[:120]
  if query:rows=rows.filter(Q(center_name__icontains=query)|Q(zip_code__icontains=query)|Q(center_location__icontains=query))
  zip_code=saved_center_zip(member.center)
  rows=rows.annotate(zip_missing=Case(When(zip_code='',then=Value(1)),default=Value(0),output_field=IntegerField()))
  if zip_code:
   rows=rows.annotate(zip_gap=Abs(Cast(NullIf('zip_code',Value('')),IntegerField())-Value(int(zip_code)))).order_by('zip_missing','zip_gap','zip_code','center_name','id')
  else:
   rows=rows.order_by('zip_missing','zip_code','center_name','id')
  paginator=Pagination();page=paginator.paginate_queryset(rows,request)
  response=paginator.get_paginated_response([{'id':r.pk,'center_name':r.center_name,'zip_code':r.zip_code,'scans_2022':r.scans_2022,'scans_2023':r.scans_2023,'scans_2024':r.scans_2024,'scans_2025':r.scans_2025,'scans_2026':r.scans_2026,'current':r.center_id==center_id} for r in page])
  fields=[f'scans_{year}' for year in range(2022,2027)]
  zip_rows=list(PreviousScanRecord.objects.filter(zip_code=zip_code).values('source_id',*fields)) if zip_code else []
  total=next((r for r in zip_rows if r['source_id']=='zip-total-'+zip_code),{})
  history={}
  for field in fields:
   value=total.get(field)
   if value is None:
    # Shared ZIP totals are repeated per center: select an agreed value, never sum.
    values={r[field] for r in zip_rows if r[field] is not None}
    value=next(iter(values)) if len(values)==1 else None
   history[field]=value
  response.data['center_zip']=zip_code
  response.data['center_zip_history']={'center_name':member.center.name,'zip_code':zip_code,**history} if zip_rows else None
  response.data['forecast_years']=[2025,2026]
  return response
