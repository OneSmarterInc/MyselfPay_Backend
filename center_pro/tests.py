from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone
from .test_helpers import OriginClient as APIClient
from .models import Center, Membership, Slot, BookingRequest, AuditEvent, WorkingHours

class BookingApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = get_user_model().objects.create_user(username='staff')
        self.owner = get_user_model().objects.create_user(username='owner')
        self.center = Center.objects.create(name='Sample center')
        self.other = Center.objects.create(name='Other center')
        for day in range(7): WorkingHours.objects.create(center=self.center,weekday=day,opens='00:00',closes='23:59')
        Membership.objects.create(user=self.staff, center=self.center, role='staff')
        Membership.objects.create(user=self.owner, center=self.center, role='owner')
        self.at = timezone.now()+timedelta(days=2)
        self.slot = Slot.objects.create(center=self.center, resource='Scanner 1', starts_at=self.at, ends_at=self.at+timedelta(minutes=30))
        self.booking = BookingRequest.objects.create(center=self.center, reference='TEST-1', patient_name='Sample Patient', exam='MRI', price_snapshot=Decimal('395.00'),preferred_slot_ids=[self.slot.id])
        self.prefix = f'/api/center-pro/centers/{self.center.id}'
        self.url = f'{self.prefix}/requests/{self.booking.id}/confirm'
        self.client.force_authenticate(self.staff)

    def test_confirm_persists_snapshot_and_audit_and_retry_is_idempotent(self):
        for _ in range(2):
            response = self.client.post(self.url, {'slot_id':self.slot.id}, format='json')
            self.assertEqual(response.status_code,200)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status,'confirmed')
        self.assertEqual(self.booking.price_snapshot,Decimal('395.00'))
        self.assertEqual(AuditEvent.objects.count(),1)

    def test_overlapping_slots_are_not_offered(self):
        self.client.post(self.url,{'slot_id':self.slot.id},format='json')
        overlapping = Slot.objects.create(center=self.center,resource='Scanner 1',starts_at=self.at+timedelta(minutes=15),ends_at=self.at+timedelta(minutes=45))
        available = Slot.objects.create(center=self.center,resource='Scanner 2',starts_at=self.at,ends_at=self.at+timedelta(minutes=30))
        response = self.client.get(self.prefix+'/slots')
        offered = [slot['id'] for slot in response.data]
        self.assertNotIn(overlapping.id,offered)
        self.assertNotIn(self.slot.id,offered)
        self.assertIn(available.id,offered)

    @override_settings(ENABLE_DEV_LOGIN=True)
    def test_development_login_remains_unavailable_in_integrated_app(self):
        self.assertEqual(APIClient().post('/api/center-pro/development-login',{'role':'staff'},format='json').status_code,404)

    def test_overlapping_resource_cannot_be_double_booked(self):
        self.client.post(self.url,{'slot_id':self.slot.id},format='json')
        other_slot=Slot.objects.create(center=self.center,resource='Scanner 1',starts_at=self.at+timedelta(minutes=15),ends_at=self.at+timedelta(minutes=45))
        other=BookingRequest.objects.create(center=self.center,reference='TEST-2',patient_name='Sample',exam='CT',price_snapshot=450,preferred_slot_ids=[other_slot.id])
        response=self.client.post(f'{self.prefix}/requests/{other.id}/confirm',{'slot_id':other_slot.id},format='json')
        self.assertEqual(response.status_code,409)
        self.assertEqual(AuditEvent.objects.count(),1)

    def test_other_center_is_not_visible(self):
        self.assertEqual(self.client.get(f'/api/center-pro/centers/{self.other.id}/requests').status_code,404)
        slot=Slot.objects.create(center=self.other,resource='Scanner 1',starts_at=self.at,ends_at=self.at+timedelta(minutes=30))
        self.assertEqual(self.client.post(self.url,{'slot_id':slot.id},format='json').status_code,404)

    def test_assigned_owner_can_confirm(self):
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.post(self.url,{'slot_id':self.slot.id},format='json').status_code,200)

    def test_blocked_and_past_slots_are_rejected(self):
        self.slot.blocked=True;self.slot.save()
        self.assertEqual(self.client.post(self.url,{'slot_id':self.slot.id},format='json').status_code,409)
        self.slot.blocked=False;self.slot.starts_at=timezone.now()-timedelta(hours=2);self.slot.ends_at=timezone.now()-timedelta(hours=1);self.slot.save()
        self.assertEqual(self.client.post(self.url,{'slot_id':self.slot.id},format='json').status_code,409)

    def test_pagination_search_and_cap(self):
        for i in range(30):
            BookingRequest.objects.create(center=self.center,reference=f'ROW-{i:03}',patient_name='Sample',exam='CT',price_snapshot=450)
        response=self.client.get(self.prefix+'/requests?page_size=1000&search=CT')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['count'],30)
        self.assertEqual(len(response.data['results']),20)
        response=self.client.get(self.prefix+'/requests?page=2&page_size=20&search=CT')
        self.assertEqual(len(response.data['results']),10)

    def test_revoked_membership_denies_access(self):
        Membership.objects.filter(user=self.staff).delete()
        self.assertEqual(self.client.get(self.prefix+'/requests').status_code,404)

    def test_anonymous_is_denied(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.prefix+'/requests').status_code,401)

    @override_settings(ENABLE_DEV_LOGIN=False)
    def test_development_login_is_disabled(self):
        self.assertEqual(self.client.post('/api/center-pro/development-login',{'role':'staff'},format='json').status_code,404)

    def test_session_authentication_enforces_csrf(self):
        client=APIClient(enforce_csrf_checks=True)
        client.force_login(self.staff)
        session=client.session;session['center_pro_verified_user']=self.staff.pk;session.save()
        self.assertEqual(client.post(self.url,{'slot_id':self.slot.id},format='json').status_code,403)

    def test_staff_sees_only_patient_choices_with_cross_resource_overlap(self):
        other_slot=Slot.objects.create(center=self.center,resource='Scanner 2',starts_at=self.at,ends_at=self.at+timedelta(minutes=30))
        BookingRequest.objects.create(center=self.center,reference='OVERLAP',patient_name='Private',exam='CT',price_snapshot=450,status='confirmed',slot=other_slot)
        response=self.client.get(self.prefix+f'/slots?booking_id={self.booking.id}')
        self.assertEqual([row['id'] for row in response.data],[self.slot.id])
        self.assertTrue(response.data[0]['selectable'])
        self.assertEqual(response.data[0]['overlap_count'],1)
        self.assertEqual(response.data[0]['appointments'][0]['reference'],'OVERLAP')
        self.assertNotIn('patient_name',response.data[0]['appointments'][0])

    def test_unsubmitted_slot_and_legacy_request_cannot_be_confirmed(self):
        extra=Slot.objects.create(center=self.center,resource='Scanner 2',starts_at=self.at,ends_at=self.at+timedelta(minutes=30))
        self.assertEqual(self.client.post(self.url,{'slot_id':extra.id},format='json').status_code,409)
        self.booking.preferred_slot_ids=[];self.booking.save()
        self.assertEqual(self.client.post(self.url,{'slot_id':self.slot.id},format='json').status_code,409)

    def test_same_resource_conflict_is_visible_and_disabled(self):
        other=Slot.objects.create(center=self.center,resource=self.slot.resource,starts_at=self.at+timedelta(minutes=10),ends_at=self.at+timedelta(minutes=40))
        BookingRequest.objects.create(center=self.center,reference='TAKEN',patient_name='Sample',exam='CT',price_snapshot=450,status='confirmed',slot=other)
        response=self.client.get(self.prefix+f'/slots?booking_id={self.booking.id}')
        self.assertFalse(response.data[0]['selectable'])
        self.assertTrue(response.data[0]['conflict'])
        self.assertEqual(self.client.post(self.url,{'slot_id':self.slot.id},format='json').status_code,409)

    def test_owner_overview_distinguishes_unavailable_metrics_from_bookings(self):
        self.client.force_authenticate(self.owner)
        data=self.client.get(self.prefix+'/overview').data
        self.assertIsNone(data['completed_scans'])
        self.assertIsNone(data['search_appearances'])
        self.assertEqual(data['total'],1)
        self.assertEqual(data['upcoming'],0)
        self.assertEqual(sum(row['count'] for row in data['request_sources']),data['total'])

    def test_confirmation_rechecks_changed_working_hours(self):
        WorkingHours.objects.filter(center=self.center).update(closed=True)
        response=self.client.post(self.url,{'slot_id':self.slot.id},format='json')
        self.assertEqual(response.status_code,409)
        self.booking.refresh_from_db()
        self.assertEqual(self.booking.status,'pending')

    def test_resource_conflict_is_case_insensitive(self):
        other=Slot.objects.create(center=self.center,resource='SCANNER 1',starts_at=self.at,ends_at=self.at+timedelta(minutes=30))
        BookingRequest.objects.create(center=self.center,reference='CASE',patient_name='Synthetic',exam='MRI',price_snapshot=395,slot=other,status='confirmed')
        self.assertEqual(self.client.post(self.url,{'slot_id':self.slot.id},format='json').status_code,409)

    def test_malformed_request_id_returns_validation_error(self):
        for value in ['invalid','-1','99999999999999999999999']:
            self.assertEqual(self.client.get(self.prefix+'/slots?booking_id='+value).status_code,400)
