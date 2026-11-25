from django.db.models import Q
from rest_framework import serializers
from rest_framework.response import Response
from .models import Membership
from .owner_views import OwnerView
from .views import Pagination
from .access import assign_staff,revoke_staff


class StaffInput(serializers.Serializer):
    name=serializers.CharField(max_length=120)
    email=serializers.EmailField(max_length=254)
    def validate_email(self,value):return value.strip().lower()


def staff_record(member):
    return {'id':member.pk,'name':member.user.get_full_name() or member.user.username,
            'email':member.user.email,'active':member.user.is_active,'role':'staff'}


class StaffView(OwnerView):
    def get(self,request,center_id):
        self.owner(request,center_id)
        rows=Membership.objects.filter(center_id=center_id,role='staff').select_related('user')
        search=request.query_params.get('search','').strip()[:120]
        if search:rows=rows.filter(Q(user__first_name__icontains=search)|Q(user__last_name__icontains=search)|Q(user__email__icontains=search))
        paginator=Pagination()
        page=paginator.paginate_queryset(rows.order_by('user__first_name','user__email','id'),request)
        return paginator.get_paginated_response([staff_record(member) for member in page])
    def post(self,request,center_id):
        self.owner(request,center_id)
        form=StaffInput(data=request.data);form.is_valid(raise_exception=True)
        member=assign_staff(request.user,center_id,form.validated_data)
        return Response(staff_record(member),status=201)


class RevokeStaffView(OwnerView):
    def post(self,request,center_id,member_id):
        self.owner(request,center_id)
        revoke_staff(request.user,center_id,member_id)
        return Response({'detail':'Staff access removed from this center.'})
