import json
from django.conf import settings
from django.contrib.auth import login, logout, get_user_model
from django.db.models import Q, Exists, OuterRef
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_GET, require_POST
from rest_framework.exceptions import ValidationError
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import AuditEvent, BookingRequest, Membership, Slot
from .serializers import BookingSerializer, ConfirmationSerializer, SlotSerializer
from .services import confirm_booking

def identity(request):
    from .integration_auth import session_payload
    return session_payload(request)

@require_GET
def session(request):
    response = JsonResponse(identity(request))
    response['Cache-Control'] = 'no-store'
    return response

@require_POST
@csrf_protect
def development_login(request):
    if not settings.ENABLE_DEV_LOGIN:
        return JsonResponse({'detail': 'Not found.'}, status=404)
    try:
        role = json.loads(request.body).get('role')
    except (ValueError, AttributeError):
        return JsonResponse({'detail': 'Invalid JSON.'}, status=400)
    if role not in ('owner', 'staff', 'admin'):
        return JsonResponse({'detail': 'Choose owner or staff.'}, status=400)
    user = get_user_model().objects.filter(username='demo-'+role, is_active=True).first()
    if not user:
        return JsonResponse({'detail': 'Run seed_demo first.'}, status=409)
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    if role == 'admin' and user.is_superuser:
        request.session['pro_admin_mfa'] = True
    return JsonResponse(identity(request))

@require_POST
@csrf_protect
def sign_out(request):
    token=request.COOKIES.get(__import__('center_pro.integration_auth',fromlist=['center_cookie']).center_cookie(),'')
    if token:
        import booking
        with booking.connection(settings.DOMAIN_DATABASE,write=True) as db:
            import center_portal
            try:center_portal.identity(db,token,request.headers.get('X-Center-CSRF',''))
            except booking.BookingError:return JsonResponse({'detail':'Center session expired.'},status=403)
            db.execute('DELETE FROM center_portal_sessions WHERE token_hash=?',(booking.digest(token),))
    logout(request)
    response=JsonResponse({'detail':'Signed out.'})
    response.delete_cookie(__import__('center_pro.integration_auth',fromlist=['center_cookie']).center_cookie(),path='/')
    return response

class CenterView(APIView):
    from .integration_auth import IntegratedAuthentication
    authentication_classes=[IntegratedAuthentication]
    def get_exception_handler(self):
        from rest_framework.views import exception_handler
        from api.exceptions import api_exception_handler
        def handler(exc,context):
            result=exception_handler(exc,context)
            return result if result is not None else api_exception_handler(exc,context)
        return handler
    def membership(self, request, center_id):
        member=get_object_or_404(Membership.objects.select_related('center'), user=request.user, center_id=center_id, center__directory_active=True)
        from .integration import sync_requests
        sync_requests(member.center)
        return member
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        return response

class Pagination(PageNumberPagination):
    page_size = 5
    page_size_query_param = 'page_size'
    max_page_size = 20

class RequestsView(CenterView):
    def get(self, request, center_id):
        self.membership(request, center_id)
        rows = BookingRequest.objects.filter(center_id=center_id).select_related('slot')
        status = request.query_params.get('status', '')
        if status:
            if status not in ('pending', 'confirmed', 'closed'):
                raise ValidationError({'status': 'Invalid status.'})
            rows = rows.filter(status=status)
        query = request.query_params.get('search', '').strip()[:120]
        if query:
            rows = rows.filter(Q(reference__icontains=query)|Q(patient_name__icontains=query)|Q(exam__icontains=query))
        order = request.query_params.get('ordering', '-created_at')
        if order not in ('created_at', '-created_at', 'reference', 'patient_name'):
            raise ValidationError({'ordering': 'Invalid sort order.'})
        rows = rows.order_by(order, 'id')
        page = Pagination()
        return page.get_paginated_response(BookingSerializer(page.paginate_queryset(rows, request), many=True).data)

class RequestView(CenterView):
    def get(self, request, center_id, booking_id):
        self.membership(request, center_id)
        return Response(BookingSerializer(get_object_or_404(BookingRequest.objects.select_related('slot'), pk=booking_id, center_id=center_id)).data)

class SlotsView(CenterView):
    def get(self, request, center_id):
        self.membership(request, center_id)
        booking_id = request.query_params.get('booking_id')
        if booking_id:
            if not booking_id.isdecimal() or len(booking_id)>18 or int(booking_id)<1:
                raise ValidationError({'booking_id':'Provide a valid request ID.'})
            booking = get_object_or_404(BookingRequest, pk=booking_id, center_id=center_id)
            if booking.preferred_windows and all(isinstance(value,dict) for value in booking.preferred_windows):
                from .preferences import preference_bounds
                groups=[]
                for index,preference in enumerate(booking.preferred_windows[:3]):
                    group={'id':f'preference-{index}',**preference,'slots':[],'detail':''}
                    try:
                        begin,end=preference_bounds(booking.center,preference)
                    except ValidationError as error:
                        group['detail']=' '.join(str(item) for item in error.detail) if isinstance(error.detail,(list,tuple)) else str(error.detail)
                        groups.append(group)
                        continue
                    page_value=request.query_params.get(f'preference_page_{index}','1')
                    if not page_value.isascii() or not page_value.isdecimal() or len(page_value)>8 or int(page_value)<1:
                        raise ValidationError('Provide a valid preference page.')
                    slots=Slot.objects.filter(center_id=center_id,starts_at__gte=begin,ends_at__lte=end,starts_at__gt=timezone.now()).order_by('starts_at','resource','id')
                    count=slots.count();page=min(int(page_value),max(1,(count+19)//20))
                    group.update({'count':count,'page':page,'page_size':20})
                    slots=slots[(page-1)*20:page*20]
                    for slot in slots:
                        overlaps=BookingRequest.objects.filter(center_id=center_id,status='confirmed',slot__starts_at__lt=slot.ends_at,slot__ends_at__gt=slot.starts_at).exclude(pk=booking.pk).select_related('slot')
                        conflict=overlaps.filter(slot__resource__iexact=slot.resource).exists()
                        item=dict(SlotSerializer(slot).data)
                        item.update({'selectable':not slot.blocked and not conflict,'conflict':conflict,'overlap_count':overlaps.count(),'appointments':[{'reference':row.reference,'starts_at':row.slot.starts_at,'ends_at':row.slot.ends_at,'resource':row.slot.resource} for row in overlaps[:5]]})
                        group['slots'].append(item)
                    if not group['slots']: group['detail']='No future slots in this period. Update availability or request another preference.'
                    groups.append(group)
                return Response(groups)
            choices = []
            choice_ids=list(booking.preferred_slot_ids[:3])
            if not choice_ids:
                from django.utils.dateparse import parse_datetime
                for index, value in enumerate(booking.preferred_windows[:3]):
                    try: at=parse_datetime(value)
                    except (ValueError, TypeError): continue
                    if not at or timezone.is_naive(at): continue
                    candidates=list(Slot.objects.filter(center_id=center_id,starts_at=at).order_by('blocked','id'))
                    slot=next((candidate for candidate in candidates if not candidate.blocked and not BookingRequest.objects.filter(center_id=center_id,status='confirmed',slot__resource__iexact=candidate.resource,slot__starts_at__lt=candidate.ends_at,slot__ends_at__gt=candidate.starts_at).exclude(pk=booking.pk).exists()), candidates[0] if candidates else None)
                    if slot:
                        choice_ids.append(slot.id)
                    else:
                        choices.append({'id':f'preference-{index}', 'starts_at':at,'ends_at':None,'resource':'Resource not assigned','selectable':False,'conflict':False,'unmatched':True,'overlap_count':0,'appointments':[]})
            for slot_id in choice_ids:
                slot = Slot.objects.filter(pk=slot_id, center_id=center_id).first()
                if not slot:
                    continue
                overlaps = BookingRequest.objects.filter(center_id=center_id, status='confirmed', slot__starts_at__lt=slot.ends_at, slot__ends_at__gt=slot.starts_at).exclude(pk=booking.pk).select_related('slot').order_by('slot__starts_at', 'id')
                conflict = overlaps.filter(slot__resource__iexact=slot.resource).exists()
                item = dict(SlotSerializer(slot).data)
                item.update({'selectable': not (slot.blocked or slot.starts_at <= timezone.now() or conflict), 'conflict': conflict, 'overlap_count': overlaps.count(), 'appointments': [{'reference': row.reference, 'starts_at': row.slot.starts_at, 'ends_at': row.slot.ends_at, 'resource': row.slot.resource} for row in overlaps[:5]]})
                choices.append(item)
            if not booking.preferred_slot_ids:
                order={value:index for index,value in enumerate(booking.preferred_windows)}
                from django.utils.dateparse import parse_datetime
                choices.sort(key=lambda item: next((index for value,index in order.items() if parse_datetime(value)==(parse_datetime(item['starts_at']) if isinstance(item['starts_at'],str) else item['starts_at'])),99))
            return Response(choices)
        overlaps = BookingRequest.objects.filter(center_id=center_id, status='confirmed', slot__resource__iexact=OuterRef('resource'), slot__starts_at__lt=OuterRef('ends_at'), slot__ends_at__gt=OuterRef('starts_at'))
        slots = Slot.objects.filter(center_id=center_id, blocked=False, starts_at__gt=timezone.now()).annotate(occupied=Exists(overlaps)).filter(occupied=False).order_by('starts_at')[:60]
        return Response(SlotSerializer(slots, many=True).data)

class ConfirmView(CenterView):
    def post(self, request, center_id, booking_id):
        self.membership(request, center_id)
        serializer = ConfirmationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = confirm_booking(user=request.user, center_id=center_id, booking_id=booking_id, slot_id=serializer.validated_data['slot_id'])
        return Response(BookingSerializer(booking).data)

class OverviewView(CenterView):
    def get(self, request, center_id):
        member = self.membership(request, center_id)
        rows = BookingRequest.objects.filter(center_id=center_id)
        data = {'pending': rows.filter(status='pending').count(), 'confirmed': rows.filter(status='confirmed').count(), 'total': rows.count(), 'revenue': None}
        if member.role == Membership.Role.OWNER:
            from django.db.models import Count
            data.update({'completed_scans': None, 'search_appearances': None, 'upcoming': rows.filter(status='confirmed',slot__starts_at__gt=timezone.now()).count(), 'request_sources': list(rows.values('source').annotate(count=Count('id')).order_by('-count','source')), 'reporting_period': 'all_time'})
        return Response(data)

class AuditView(CenterView):
    def get(self, request, center_id):
        self.membership(request, center_id)
        rows = AuditEvent.objects.filter(center_id=center_id).order_by('-created_at', '-id')
        paginator = Pagination()
        page = paginator.paginate_queryset(rows, request)
        return paginator.get_paginated_response([{'id': r.id, 'action': r.action, 'reference': r.reference, 'created_at': r.created_at} for r in page])


class AlternativeTimeView(CenterView):
    def post(self,request,center_id,booking_id):
        from rest_framework import serializers
        from .services import confirm_alternative
        member=self.membership(request,center_id)
        from zoneinfo import ZoneInfo
        class Input(serializers.Serializer):
            starts_at=serializers.DateTimeField(default_timezone=ZoneInfo(member.center.timezone))
            ends_at=serializers.DateTimeField(default_timezone=ZoneInfo(member.center.timezone))
            resource=serializers.CharField(max_length=80)
            reason=serializers.CharField(max_length=500)
            patient_agreed=serializers.BooleanField()
        data=Input(data=request.data);data.is_valid(raise_exception=True)
        values=data.validated_data
        if not values.pop('patient_agreed'):
            raise ValidationError('Confirm that the patient agreed to this alternative time.')
        booking=confirm_alternative(user=request.user,center_id=center_id,booking_id=booking_id,**values)
        return Response(BookingSerializer(booking).data)
