import hashlib
import secrets
from datetime import datetime, time, timedelta
from decimal import Decimal
from io import BytesIO
from zoneinfo import ZoneInfo
import qrcode
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Center, Membership, Slot, BookingRequest, AuditEvent, Offer, WorkingHours, PatientLink, EstimateDraft
from .views import CenterView, Pagination
from .owner_views import OwnerView
from .services import Conflict


class StaffView(CenterView):
    def staff(self,request,center_id):
        member=self.membership(request,center_id)
        if member.role!='staff':raise PermissionDenied('Staff access required.')
        return member


class AdminView(CenterView):
    def check_admin(self,request):
        from .integration_auth import admin_verified
        if not request.user.is_superuser or not admin_verified(request):
            raise PermissionDenied('Super-admin authentication with MFA is required.')


class AdminCenters(AdminView):
    def get(self,request):
        self.check_admin(request)
        rows=Center.objects.filter(directory_active=True).order_by('name').filter(name__icontains=request.query_params.get('search','')[:120])
        page=Pagination();items=page.paginate_queryset(rows,request)
        return page.get_paginated_response([{'id':c.id,'name':c.name,'timezone':c.timezone,'owners':Membership.objects.filter(center=c,role='owner').count(),'staff':Membership.objects.filter(center=c,role='staff').count()} for c in items])


class AssignmentInput(serializers.Serializer):
    email=serializers.EmailField(max_length=254)
    name=serializers.CharField(max_length=120)
    role=serializers.ChoiceField(choices=['owner','staff'])


class Assignments(AdminView):
    def get(self,request,center_id):
        self.check_admin(request);get_object_or_404(Center,pk=center_id)
        page=Pagination();rows=page.paginate_queryset(Membership.objects.filter(center_id=center_id).select_related('user').order_by('role','id'),request)
        return page.get_paginated_response([{'id':m.id,'name':m.user.get_full_name() or m.user.username,'email':m.user.email,'role':m.role} for m in rows])
    def post(self,request,center_id):
        from .access import assign_access
        self.check_admin(request)
        form=AssignmentInput(data=request.data);form.is_valid(raise_exception=True)
        member=assign_access(request.user,center_id,form.validated_data)
        return Response({'id':member.id,'detail':'Access assigned. No invitation email was sent.'},status=201)


class RevokeAssignment(AdminView):
    def post(self,request,center_id,member_id):
        from .access import revoke_access
        self.check_admin(request);revoke_access(request.user,center_id,member_id)
        return Response({'detail':'Access revoked.'})


class AdminAudit(AdminView):
    def get(self,request):
        self.check_admin(request);page=Pagination();rows=page.paginate_queryset(AuditEvent.objects.select_related('center','actor').order_by('-created_at','-id'),request)
        return page.get_paginated_response([{'id':r.id,'center':r.center.name,'actor':r.actor.username,'action':r.action,'reference':r.reference,'created_at':r.created_at} for r in rows])


class HoursInput(serializers.Serializer):
    weekday=serializers.IntegerField(min_value=0,max_value=6)
    opens=serializers.TimeField()
    closes=serializers.TimeField()
    closed=serializers.BooleanField()
    def validate(self,data):
        if not data['closed'] and data['closes']<=data['opens']:raise serializers.ValidationError('Closing time must be after opening time.')
        return data


class HoursView(StaffView):
    def get(self,request,center_id):
        self.staff(request,center_id)
        saved={h.weekday:h for h in WorkingHours.objects.filter(center_id=center_id)}
        return Response([{'weekday':i,'opens':saved[i].opens if i in saved else '08:00','closes':saved[i].closes if i in saved else '17:00','closed':saved[i].closed if i in saved else i>=5} for i in range(7)])
    @transaction.atomic
    def post(self,request,center_id):
        self.staff(request,center_id);get_object_or_404(Center.objects.select_for_update(),pk=center_id)
        form=HoursInput(data=request.data,many=True);form.is_valid(raise_exception=True)
        if len(form.validated_data)!=7 or {d['weekday'] for d in form.validated_data}!=set(range(7)):raise ValidationError('Provide all seven weekdays exactly once.')
        for row in form.validated_data:
            day=row.pop('weekday');WorkingHours.objects.update_or_create(center_id=center_id,weekday=day,defaults=row)
        AuditEvent.objects.create(center_id=center_id,actor=request.user,action='schedule.hours_updated',reference='WEEKLY-HOURS')
        return Response({'detail':'Weekly hours saved. Existing appointments are unchanged.'})


class NewSlotInput(serializers.Serializer):
    starts_at=serializers.DateTimeField()
    ends_at=serializers.DateTimeField()
    resource=serializers.CharField(max_length=80)
    def validate(self,data):
        if data['starts_at']<=timezone.now() or data['ends_at']<=data['starts_at']:raise serializers.ValidationError('Choose a future start and a later end time.')
        if data['ends_at']-data['starts_at']>timedelta(hours=8):raise serializers.ValidationError('A slot cannot exceed eight hours.')
        return data


class AvailabilityView(StaffView):
    def get(self,request,center_id):
        self.staff(request,center_id)
        rows=Slot.objects.filter(center_id=center_id,ends_at__gt=timezone.now()).select_related('booking').order_by('starts_at','id')
        page=Pagination();items=page.paginate_queryset(rows,request)
        return page.get_paginated_response([{'id':s.id,'starts_at':s.starts_at,'ends_at':s.ends_at,'resource':s.resource,'blocked':s.blocked,'confirmed':hasattr(s,'booking')} for s in items])
    @transaction.atomic
    def post(self,request,center_id):
        self.staff(request,center_id);center=get_object_or_404(Center.objects.select_for_update(),pk=center_id)
        form=NewSlotInput(data=request.data)
        form.fields['starts_at'].timezone=ZoneInfo(center.timezone)
        form.fields['ends_at'].timezone=ZoneInfo(center.timezone)
        form.is_valid(raise_exception=True);data=form.validated_data
        zone=ZoneInfo(center.timezone)
        for value in (data['starts_at'],data['ends_at']):
            local=value.astimezone(zone)
            roundtrip=local.astimezone(ZoneInfo('UTC')).astimezone(zone)
            if local.replace(tzinfo=None)!=roundtrip.replace(tzinfo=None) or local.replace(fold=0).utcoffset()!=local.replace(fold=1).utcoffset():
                raise ValidationError('Choose a time outside the daylight-saving transition hour.')
        local_start=data['starts_at'].astimezone(zone);local_end=data['ends_at'].astimezone(zone)
        hours=WorkingHours.objects.filter(center=center,weekday=local_start.weekday()).first()
        if not hours:raise ValidationError('Save weekly working hours before adding slots.')
        if hours.closed or local_start.date()!=local_end.date() or local_start.time()<hours.opens or local_end.time()>hours.closes:raise ValidationError('The slot must be within this center’s saved working hours.')
        if Slot.objects.filter(center=center,resource__iexact=data['resource'],starts_at__lt=data['ends_at'],ends_at__gt=data['starts_at']).exists():raise Conflict('This resource already has an overlapping slot.')
        slot=Slot.objects.create(center=center,**data);AuditEvent.objects.create(center=center,actor=request.user,action='schedule.slot_added',reference=f'SLOT-{slot.id}')
        return Response({'id':slot.id},status=201)


class BlockSlot(StaffView):
    @transaction.atomic
    def post(self,request,center_id,slot_id):
        self.staff(request,center_id);get_object_or_404(Center.objects.select_for_update(),pk=center_id)
        slot=get_object_or_404(Slot,center_id=center_id,pk=slot_id)
        if BookingRequest.objects.filter(slot=slot).exists():raise Conflict('A confirmed appointment cannot be blocked.')
        if type(request.data.get('blocked')) is not bool:raise ValidationError('Provide a blocked boolean.')
        slot.blocked=request.data['blocked'];slot.save(update_fields=['blocked'])
        AuditEvent.objects.create(center_id=center_id,actor=request.user,action='schedule.slot_blocked' if slot.blocked else 'schedule.slot_unblocked',reference=f'SLOT-{slot.id}')
        return Response({'detail':'Availability updated.'})


class OffersForStaff(StaffView):
    def get(self,request,center_id):
        self.staff(request,center_id)
        return Response(list(Offer.objects.filter(center_id=center_id).order_by('exam').values('id','exam','price')[:200]))


class RescueInput(serializers.Serializer):
    offer_id=serializers.IntegerField(min_value=1)
    consent=serializers.BooleanField()
    def validate_consent(self,value):
        if not value:raise serializers.ValidationError('Record patient consent before creating a self-pay link.')
        return value


class RescueView(StaffView):
    def post(self,request,center_id):
        self.staff(request,center_id);form=RescueInput(data=request.data);form.is_valid(raise_exception=True)
        offer=get_object_or_404(Offer,pk=form.validated_data['offer_id'],center_id=center_id)
        raw=secrets.token_urlsafe(32)
        link=PatientLink.objects.create(center_id=center_id,offer=offer,digest=hashlib.sha256(raw.encode()).hexdigest(),price_snapshot=offer.price,expires_at=timezone.now()+timedelta(days=7),consent=True,created_by=request.user)
        AuditEvent.objects.create(center_id=center_id,actor=request.user,action='patient.link_created',reference=f'LINK-{link.id}')
        return Response({'url':settings.FRONTEND_ORIGIN+'/#center-pro/patient/'+raw,'expires_at':link.expires_at,'delivery':'not_sent','detail':'Link created. SMS delivery is not configured; no message was sent.'},status=201)


def validate_patient_working_time(center_id, at):
    from zoneinfo import ZoneInfo
    local=at.astimezone(ZoneInfo(Center.objects.get(pk=center_id).timezone))
    hours=WorkingHours.objects.filter(center_id=center_id,weekday=local.weekday()).first()
    if not hours:
        raise serializers.ValidationError('Working hours are not available for this day. Contact the center.')
    if hours.closed:
        raise serializers.ValidationError('The center is closed on the selected day. Choose another date.')
    if not hours.opens <= local.time().replace(tzinfo=None) < hours.closes:
        raise serializers.ValidationError(f'Choose a time between {hours.opens:%H:%M} and {hours.closes:%H:%M}, before closing, in the center timezone.')


class PatientInput(serializers.Serializer):
    patient_name=serializers.CharField(max_length=120)
    preferred_slot_ids=serializers.ListField(child=serializers.IntegerField(min_value=1),min_length=3,max_length=3,required=False)
    preferences=serializers.ListField(child=serializers.DictField(),min_length=3,max_length=3,required=False)
    preferred_times=serializers.ListField(child=serializers.CharField(max_length=32),min_length=3,max_length=3,required=False)
    def validate(self, data):
        from datetime import datetime, timezone as dt_timezone
        from zoneinfo import ZoneInfo
        if sum(key in data for key in ('preferences','preferred_times','preferred_slot_ids')) != 1:
            raise serializers.ValidationError('Provide three date and period preferences.')
        if 'preferences' in data:
            from .preferences import preference_bounds
            center=Center.objects.get(pk=self.context['center_id'])
            values=data.pop('preferences')
            for value in values:
                begin,end=preference_bounds(center,value)
                if end<=timezone.now():
                    raise serializers.ValidationError('Choose a period that has not ended.')
            normalized=[{'date':v['date'],'period':v['period']} for v in values]
            if len({(v['date'],v['period']) for v in normalized})!=3:
                raise serializers.ValidationError('Choose three different date and period combinations.')
            data['preferred_windows']=normalized
            return data
        if 'preferred_times' not in data:
            return data
        zone=ZoneInfo(Center.objects.get(pk=self.context['center_id']).timezone)
        values=[]
        for raw in data.pop('preferred_times'):
            try:
                local=datetime.fromisoformat(raw)
                if local.tzinfo is not None:
                    raise ValueError()
                candidates={local.replace(tzinfo=zone,fold=f).astimezone(dt_timezone.utc) for f in (0,1) if local.replace(tzinfo=zone,fold=f).astimezone(dt_timezone.utc).astimezone(zone).replace(tzinfo=None)==local}
                if len(candidates)!=1:
                    raise ValueError()
                at=candidates.pop()
                if at<=timezone.now():
                    raise ValueError()
                validate_patient_working_time(self.context["center_id"],at)
                values.append(at.isoformat())
            except (ValueError, TypeError):
                raise serializers.ValidationError('Enter valid future times in the center timezone. Avoid ambiguous daylight-saving times.')
        if len(set(values))!=3:
            raise serializers.ValidationError('Enter three different dates and times.')
        data['preferred_windows']=values
        return data
    def validate_preferred_slot_ids(self, value):
        if len(set(value)) != 3:
            raise serializers.ValidationError('Choose three different slots.')
        if Slot.objects.filter(center_id=self.context['center_id'], pk__in=value, blocked=False, starts_at__gt=timezone.now()).count() != 3:
            raise serializers.ValidationError('Choose three future slots from this center.')
        for slot in Slot.objects.filter(center_id=self.context['center_id'],pk__in=value):
            validate_patient_working_time(self.context['center_id'],slot.starts_at)
        return value


class PatientView(APIView):
    permission_classes=[AllowAny]
    authentication_classes=[]
    def link(self,token):
        return get_object_or_404(PatientLink.objects.select_related('offer','center','booking','booking__slot'),digest=hashlib.sha256(token.encode()).hexdigest(),expires_at__gt=timezone.now())
    def get(self,request,token):
        link=self.link(token)
        data={'center':link.center.name,'timezone':link.center.timezone,'exam':link.offer.exam,'price':str(link.price_snapshot),'status':link.booking.status if link.booking else 'not_requested','reference':link.booking.reference if link.booking else None,'appointment':link.booking.slot.starts_at if link.booking and link.booking.slot else None,'estimate_status':'Draft estimate only; approved GFE delivery is not connected.'}
        data['working_hours']=list(WorkingHours.objects.filter(center=link.center).order_by('weekday').values('weekday','opens','closes','closed'))
        return Response(data,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})
    @transaction.atomic
    def post(self,request,token):
        link=self.link(token);link=PatientLink.objects.select_for_update().get(pk=link.pk)
        if link.booking_id:return Response({'reference':link.booking.reference,'detail':'Your request is already with the center.'})
        form=PatientInput(data=request.data,context={'center_id':link.center_id});form.is_valid(raise_exception=True)
        booking=BookingRequest.objects.create(center=link.center,exam=link.offer.exam,price_snapshot=link.price_snapshot,source='cash_rescue',reference='REQ-'+secrets.token_hex(6).upper(),**form.validated_data)
        link.booking=booking;link.save(update_fields=['booking'])
        return Response({'reference':booking.reference,'detail':'Request received. Staff must confirm an appointment.'},status=201,headers={'Cache-Control':'no-store'})


class EstimatesView(StaffView):
    def get(self,request,center_id,booking_id):
        self.staff(request,center_id);booking=get_object_or_404(BookingRequest,pk=booking_id,center_id=center_id)
        rows=EstimateDraft.objects.filter(booking=booking).order_by('-version')
        page=Pagination();items=page.paginate_queryset(rows,request)
        return page.get_paginated_response([{'id':r.id,'version':r.version,'snapshot':r.snapshot,'created_at':r.created_at} for r in items])
    @transaction.atomic
    def post(self,request,center_id,booking_id):
        self.staff(request,center_id);booking=get_object_or_404(BookingRequest.objects.select_for_update().select_related('center'),pk=booking_id,center_id=center_id)
        last=EstimateDraft.objects.filter(booking=booking).order_by('-version').first()
        snapshot={'patient':booking.patient_name,'exam':booking.exam,'price':str(booking.price_snapshot),'center':booking.center.name,'reference':booking.reference,'status':'DRAFT — NOT AN ISSUED GOOD FAITH ESTIMATE','notice':'Official template, provider identifiers, itemization and delivery timing require approval before issuing a GFE.'}
        item=EstimateDraft.objects.create(booking=booking,version=(last.version+1 if last else 1),snapshot=snapshot,created_by=request.user)
        AuditEvent.objects.create(center_id=center_id,actor=request.user,action='estimate.drafted',reference=f'DRAFT-{item.id}')
        return Response({'id':item.id,'version':item.version,'snapshot':item.snapshot},status=201)


class MarketView(OwnerView):
    def get(self,request,center_id):
        self.owner(request,center_id)
        return Response({'is_sample':False,'source':'Market data unavailable','estimated_scans':None,'illustrative_price':None,'benchmark_date':None,'searches':None,'employers':None})


class SubscriptionView(OwnerView):
    def get(self,request,center_id):
        self.owner(request,center_id)
        return Response({'plan':'Center Pro','monthly_price':'100.00','currency':'USD','status':'not_connected','invoices':[],'detail':'Stripe configuration and billing-account mapping are required. No subscription has been created or charged.'})


class DigestView(OwnerView):
    def get(self,request,center_id):
        member=self.owner(request,center_id)
        since=timezone.now()-timedelta(days=30)
        bookings=BookingRequest.objects.filter(center_id=center_id,created_at__gte=since)
        return Response({'center':member.center.name,'period_start':since,'period_end':timezone.now(),'requests':bookings.count(),'confirmed':bookings.filter(status='confirmed').count(),'outreach_activities':AuditEvent.objects.filter(center_id=center_id,action__startswith='outreach.',created_at__gte=since).count(),'revenue':None,'delivery':'not_sent'})


class ReferralView(OwnerView):
    def get(self,request,center_id):
        member=self.owner(request,center_id)
        url=settings.FRONTEND_ORIGIN+'/#center-pro/referral/'+str(center_id)
        from qrcode.image.svg import SvgPathImage
        image=qrcode.make(url,image_factory=SvgPathImage);buffer=BytesIO();image.save(buffer)
        import base64
        return Response({'center':member.center.name,'url':url,'qr':'data:image/svg+xml;base64,'+base64.b64encode(buffer.getvalue()).decode(),'local_only':settings.DEBUG})


class ReferralPublic(APIView):
    permission_classes=[AllowAny];authentication_classes=[]
    def get(self,request,center_id):
        center=get_object_or_404(Center,pk=center_id)
        return Response({'center':center.name,'timezone':center.timezone,'working_hours':list(WorkingHours.objects.filter(center=center).order_by('weekday').values('weekday','opens','closes','closed')),'offers':list(Offer.objects.filter(center=center).order_by('exam').values('id','exam','price')[:200])})
    @transaction.atomic
    def post(self,request,center_id):
        from .auth_flows import allowed
        if not allowed('referral:'+request.META.get('REMOTE_ADDR',''),20):return Response({'detail':'Please try again later.'},status=429)
        center=get_object_or_404(Center,pk=center_id)
        class Form(PatientInput):
            offer_id=serializers.IntegerField(min_value=1)
        form=Form(data=request.data,context={'center_id':center.id});form.is_valid(raise_exception=True);data=form.validated_data
        offer=get_object_or_404(Offer,pk=data.pop('offer_id'),center=center)
        booking=BookingRequest.objects.create(center=center,exam=offer.exam,price_snapshot=offer.price,source='qr_referral',reference='REF-'+secrets.token_hex(6).upper(),**data)
        raw=secrets.token_urlsafe(32)
        PatientLink.objects.create(center=center,offer=offer,digest=hashlib.sha256(raw.encode()).hexdigest(),price_snapshot=booking.price_snapshot,expires_at=timezone.now()+timedelta(days=7),booking=booking,consent=False)
        return Response({'reference':booking.reference,'status_url':settings.FRONTEND_ORIGIN+'/#center-pro/patient/'+raw,'detail':'Request received. This is not an appointment confirmation.'},status=201,headers={'Cache-Control':'no-store'})


def patient_slots(center_id):
    # Only scheduling metadata is public; no appointment or patient identifiers.
    return list(Slot.objects.filter(center_id=center_id, blocked=False, starts_at__gt=timezone.now()).order_by('starts_at', 'id').values('id', 'starts_at', 'ends_at', 'resource')[:200])
