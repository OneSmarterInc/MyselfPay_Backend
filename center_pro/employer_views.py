from django.db.models import Q,F
from rest_framework.response import Response
from .views import CenterView,Pagination
from .models import NearbyCompany,EmployerDirectory

class NearbyCompaniesView(CenterView):
    def get(self,request,center_id):
        self.membership(request,center_id)
        rows=NearbyCompany.objects.filter(directory__center_id=center_id).filter(Q(distance_miles__lte=100)|Q(distance_miles__isnull=True))
        query=request.query_params.get('search','').strip()[:120]
        if query:rows=rows.filter(Q(company_name__icontains=query)|Q(company_address__icontains=query)|Q(public_email__icontains=query))
        rows=rows.order_by(F('distance_miles').asc(nulls_last=True),'company_name','id')
        page=Pagination();items=page.paginate_queryset(rows,request)
        fields=['id','company_name','company_address','public_email','email_type','business_phone','employee_count','distance_miles','distance_text','location_distance_status','verification_status','source_links']
        response=page.get_paginated_response([{field:getattr(row,field) for field in fields} for row in items])
        response.data['operators']=list(EmployerDirectory.objects.filter(center_id=center_id).values('operator_name','operator_location'))
        return response
