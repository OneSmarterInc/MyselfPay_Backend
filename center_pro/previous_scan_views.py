from django.db.models import Q,Case,When,Value,IntegerField
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
   rows=rows.annotate(zip_priority=Case(When(zip_code=zip_code,then=Value(0)),default=Value(1),output_field=IntegerField())).order_by('zip_priority','zip_missing','zip_code','center_name','id')
  else:
   rows=rows.order_by('zip_missing','zip_code','center_name','id')
  paginator=Pagination();page=paginator.paginate_queryset(rows,request)
  response=paginator.get_paginated_response([{'id':r.pk,'center_name':r.center_name,'zip_code':r.zip_code,'scans_2022':r.scans_2022,'scans_2023':r.scans_2023,'scans_2024':r.scans_2024,'current':r.center_id==center_id} for r in page])
  total=PreviousScanRecord.objects.filter(source_id='zip-total-'+zip_code).first() if zip_code else None
  if not total and zip_code:total=PreviousScanRecord.objects.filter(zip_code=zip_code).first()
  response.data['center_zip']=zip_code
  response.data['center_zip_history']={'center_name':member.center.name,'zip_code':zip_code,'scans_2022':total.scans_2022,'scans_2023':total.scans_2023,'scans_2024':total.scans_2024} if total else None
  return response
