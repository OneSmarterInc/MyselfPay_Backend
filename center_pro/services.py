from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, NotFound
from .models import AuditEvent, BookingRequest, Membership, Slot, Center, WorkingHours

class Conflict(APIException):
    status_code = 409
    default_detail = 'This time is no longer available. Choose another slot.'

@transaction.atomic
def confirm_booking(*, user, center_id, booking_id, slot_id, alternative_reason=None):
    # Lock one center first, serializing conflicting confirmations across its resources.
    try:
        center = Center.objects.select_for_update().get(pk=center_id)
    except Center.DoesNotExist:
        raise NotFound()
    if not Membership.objects.filter(center=center, user=user, role__in=[Membership.Role.STAFF, Membership.Role.OWNER]).exists():
        raise PermissionDenied('Assigned owner or staff access is required to confirm appointments.')
    try:
        booking = BookingRequest.objects.select_for_update().get(pk=booking_id, center=center)
        slot = Slot.objects.get(pk=slot_id, center=center)
    except (BookingRequest.DoesNotExist, Slot.DoesNotExist):
        raise NotFound()
    if booking.status == BookingRequest.Status.CONFIRMED:
        if booking.slot_id == slot.id:
            return booking  # Safe retry after a lost response: no duplicate audit event.
        raise Conflict('This appointment is already confirmed. Rescheduling is not implemented yet.')
    if booking.status == 'closed':
        raise Conflict('This request is closed. Refresh the queue.')
    from django.utils.dateparse import parse_datetime
    preferred_times=[]
    for value in booking.preferred_windows:
        try:
            at=parse_datetime(value)
            if at and timezone.is_aware(at): preferred_times.append(at)
        except (ValueError, TypeError): pass
    from .preferences import preference_bounds
    from rest_framework.exceptions import ValidationError
    period_match=False
    for preference in booking.preferred_windows:
        if isinstance(preference,dict):
            try:
                begin,end=preference_bounds(center,preference)
                period_match=period_match or (begin<=slot.starts_at and slot.ends_at<=end)
            except ValidationError: pass
    if not alternative_reason and slot.id not in booking.preferred_slot_ids and slot.starts_at not in preferred_times and not period_match:
        raise Conflict('Select one of the patient-submitted times. New choices are required for legacy requests.')
    if slot.blocked or slot.starts_at <= timezone.now():
        raise Conflict()
    from zoneinfo import ZoneInfo
    start=slot.starts_at.astimezone(ZoneInfo(center.timezone))
    end=slot.ends_at.astimezone(ZoneInfo(center.timezone))
    hours=WorkingHours.objects.filter(center=center,weekday=start.weekday()).first()
    if not alternative_reason and (not hours or hours.closed or start.date()!=end.date() or start.time()<hours.opens or end.time()>hours.closes):
        raise Conflict('This slot is outside the current working hours. Review availability before confirming.')
    overlap = BookingRequest.objects.filter(center=center, status='confirmed', slot__resource__iexact=slot.resource, slot__starts_at__lt=slot.ends_at, slot__ends_at__gt=slot.starts_at)
    if overlap.exists():
        raise Conflict()
    from .integration import confirm_existing
    confirm_existing(booking,slot)
    booking.slot = slot
    booking.status = BookingRequest.Status.CONFIRMED
    booking.confirmed_by = user
    booking.confirmed_at = timezone.now()
    booking.save(update_fields=['slot', 'status', 'confirmed_by', 'confirmed_at'])
    AuditEvent.objects.create(center=center, actor=user, action='appointment.confirmed', reference=booking.reference)
    if alternative_reason:
        AuditEvent.objects.create(center=center, actor=user, action='appointment.alternative_time', reference=booking.reference)
    return booking


@transaction.atomic
def confirm_alternative(*,user,center_id,booking_id,starts_at,ends_at,resource,reason):
    center=Center.objects.select_for_update().get(pk=center_id)
    if not Membership.objects.filter(center=center,user=user,role__in=['owner','staff']).exists():
        raise PermissionDenied('Assigned owner or staff access is required.')
    from rest_framework.exceptions import ValidationError
    if not reason.strip() or not resource.strip():
        raise ValidationError('Enter a resource and a reason for the alternative time.')
    if not timezone.is_aware(starts_at) or not timezone.is_aware(ends_at) or starts_at<=timezone.now() or ends_at<=starts_at or ends_at-starts_at>__import__('datetime').timedelta(hours=12):
        raise ValidationError('Choose a future appointment with an end after its start, lasting up to 12 hours.')
    booking=BookingRequest.objects.select_for_update().get(pk=booking_id,center=center)
    if booking.status!='pending':
        raise Conflict('This request is no longer pending. Refresh the request.')
    slot,created=Slot.objects.get_or_create(center=center,starts_at=starts_at,resource=resource.strip(),defaults={'ends_at':ends_at})
    if slot.ends_at!=ends_at:
        raise Conflict('An existing slot has a different duration. Choose another time.')
    return confirm_booking(user=user,center_id=center_id,booking_id=booking_id,slot_id=slot.pk,alternative_reason=reason.strip())
