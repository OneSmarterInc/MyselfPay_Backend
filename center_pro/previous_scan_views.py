from django.db.models import Q,Case,When,Value,IntegerField
from rest_framework.exceptions import PermissionDenied
from .views import CenterView,Pagination
from .models import PreviousScanRecord

class PreviousScansView(CenterView):
 def get(self,request,center_id):
  member=self.membership(request,center_id)
  if member.role!='owner':raise PermissionDenied('Owner access is required.')
  rows=PreviousScanRecord.objects.all()
  query=request.query_params.get('search','').strip()[:120]
  if query:rows=rows.filter(Q(center_name__icontains=query)|Q(zip_code__icontains=query)|Q(center_location__icontains=query))
  rows=rows.annotate(current_order=Case(When(center_id=center_id,then=Value(0)),default=Value(1),output_field=IntegerField())).order_by('current_order','center_name','zip_code','id')
  paginator=Pagination();page=paginator.paginate_queryset(rows,request)
  return paginator.get_paginated_response([{'id':r.pk,'center_name':r.center_name,'zip_code':r.zip_code,'scans_2022':r.scans_2022,'scans_2023':r.scans_2023,'scans_2024':r.scans_2024,'current':r.center_id==center_id} for r in page])
